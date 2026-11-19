"""Local history store: an append-only `index.jsonl` plus full compare reports.

Layout and rules: docs/design/phase-7.md §2. Reads are strict and report every bad line by
number, never by value. Appends rewrite the whole index atomically, so a crash can't leave a
torn line.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from evalgate._json import loads_strict
from evalgate.comparison.history import summarize_report, summarize_run
from evalgate.io.reader import InputError, _error, load_run
from evalgate.io.writer import _atomic_write
from evalgate.models import Diagnostic, Report
from evalgate.models.history import (
    HISTORY_VERSION,
    MAX_LABEL,
    CompareEntry,
    HistoryEntry,
    RunEntry,
)
from evalgate.validation import validate_run

INDEX = "index.jsonl"
REPORTS = "reports"
_ENTRY: TypeAdapter[HistoryEntry] = TypeAdapter(HistoryEntry)


def report_path(directory: Path, entry_id: int) -> Path:
    return directory / REPORTS / f"{entry_id:06d}.json"


def _report_text(report: Report) -> str:
    return report.model_dump_json(indent=2) + "\n"  # the same bytes as `--json`


def _parse(index: Path, text: str) -> list[HistoryEntry]:
    entries: list[HistoryEntry] = []
    diagnostics: list[Diagnostic] = []
    last = 0
    # Split on LF only: str.splitlines() also splits on U+2028/U+2029/U+0085, which JSON keeps
    # raw inside strings, so a label holding one would break the store.
    for n, raw in enumerate(text.split("\n"), 1):
        raw = raw.removesuffix("\r")
        if not raw.strip():
            continue
        where = f"{index}: line {n}"
        try:
            data = loads_strict(raw)
        except (ValueError, RecursionError) as exc:
            msg = getattr(exc, "msg", None) or str(exc)  # a position or fixed text, no content
            diagnostics.append(_error("history_parse", f"{where}: not valid JSON: {msg}"))
            continue
        if isinstance(data, dict) and data.get("history_version") is not HISTORY_VERSION:
            what = "missing" if "history_version" not in data else "unsupported"
            message = f"{where}: {what} history_version (expected 1)"
            diagnostics.append(_error("history_parse", message))
            continue
        try:
            entry = _ENTRY.validate_python(data)
        except ValidationError as exc:
            for e in exc.errors():  # location and type only: never the input value
                loc = ".".join(map(str, e["loc"])) or "<line>"
                diagnostics.append(_error("history_parse", f"{where}: {loc}: {e['type']}"))
            continue
        if entry.id <= last:
            diagnostics.append(
                _error("history_parse", f"{where}: id {entry.id} is not above {last}")
            )
            continue
        last = entry.id
        entries.append(entry)
    if diagnostics:
        raise InputError(diagnostics)
    return entries


def _index_text(directory: Path) -> str | None:
    index = directory / INDEX
    if not index.exists():
        return None
    try:
        return index.read_text(encoding="utf-8").removeprefix("﻿")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError([_error("history_parse", f"{index}: unreadable: {exc}")]) from None


def read_history(directory: str | Path) -> list[HistoryEntry]:
    """Every entry, in id order. Raises InputError (`history_missing`, `history_parse`)."""
    directory = Path(directory)
    text = _index_text(directory)
    if text is None:
        raise InputError([_error("history_missing", f"{directory}: no {INDEX} (empty store)")])
    return _parse(directory / INDEX, text)


def _timestamp(recorded_at: datetime | None) -> str:
    if recorded_at is None:
        recorded_at = datetime.now(UTC)
    elif recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be timezone-aware")
    return recorded_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(
    directory: Path,
    fields: dict[str, object],
    report_text: str | None,
    label: str | None,
    recorded_at: datetime | None,
) -> HistoryEntry:
    if label is not None and len(label) > MAX_LABEL:
        raise InputError([_error("history_record", f"label longer than {MAX_LABEL} characters")])
    text = _index_text(directory) or ""
    entries = _parse(directory / INDEX, text)  # a corrupt store is never appended to
    entry_id = entries[-1].id + 1 if entries else 1
    if report_text is not None:
        fields = fields | {"report_sha256": hashlib.sha256(report_text.encode()).hexdigest()}
    entry = _ENTRY.validate_python(
        {"history_version": HISTORY_VERSION, "id": entry_id, "recorded_at": _timestamp(recorded_at)}
        | ({"label": label} if label is not None else {})
        | fields
    )
    lines = [x.removesuffix("\r") for x in text.split("\n") if x.strip()]  # CRLF, no final LF
    lines.append(entry.model_dump_json(exclude_defaults=True))
    # shortcut: no locking; two concurrent appends can pick the same id and the later rewrite
    # wins (report_sha256 catches a swapped report). Add a lock file if shared use appears.
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if report_text is not None:
            report_path(directory, entry_id).parent.mkdir(exist_ok=True)
            _atomic_write(report_path(directory, entry_id), report_text)
        _atomic_write(directory / INDEX, "".join(f"{line}\n" for line in lines))
    except OSError as exc:
        raise InputError(
            [_error("history_write", f"{directory}: cannot write history: {exc}")]
        ) from None
    return entry


def record_report(
    report: Report,
    directory: str | Path,
    *,
    label: str | None = None,
    recorded_at: datetime | None = None,
) -> CompareEntry:
    """Append a compare entry (any status, INVALID included) and store the report."""
    entry = _append(
        Path(directory), summarize_report(report), _report_text(report), label, recorded_at
    )
    assert isinstance(entry, CompareEntry)
    return entry


def record_run(
    run_dir: str | Path,
    directory: str | Path,
    *,
    label: str | None = None,
    recorded_at: datetime | None = None,
) -> RunEntry:
    """Append a summary of a valid run. A run that doesn't load reports the reader's codes; an
    INVALID run is not recorded (`history_record`)."""
    run = load_run(Path(run_dir))
    if errors := [d for d in validate_run(run, str(run_dir)) if d.level == "error"]:
        raise InputError(
            [*errors, _error("history_record", f"{run_dir}: run is INVALID; not recorded")]
        )
    try:
        fields = summarize_run(run)
    except ValueError as exc:
        raise InputError([_error("history_record", f"{run_dir}: {exc}")]) from None
    entry = _append(Path(directory), fields, None, label, recorded_at)
    assert isinstance(entry, RunEntry)
    return entry


def load_entry_report(directory: str | Path, entry_id: int) -> tuple[CompareEntry, Report]:
    """A compare entry and its stored report, after checking the report's hash."""
    directory = Path(directory)
    entry = next((e for e in read_history(directory) if e.id == entry_id), None)
    if entry is None:
        raise InputError([_error("history_id", f"{directory}: no entry with id {entry_id}")])
    if not isinstance(entry, CompareEntry):
        raise InputError([_error("history_id", f"entry {entry_id} is a run entry, not a compare")])
    path = report_path(directory, entry_id)
    try:
        data = path.read_bytes()
    except OSError:
        raise InputError([_error("history_id", f"{path}: report file missing")]) from None
    if hashlib.sha256(data).hexdigest() != entry.report_sha256:
        raise InputError([_error("history_id", f"{path}: report doesn't match its entry")])
    return entry, Report.model_validate_json(data)
