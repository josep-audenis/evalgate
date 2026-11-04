"""Convert a foreign results file (CSV / JSON / JSONL) into an EvalGate run via a mapping.

Fail closed: every bad record is reported, and nothing is written unless all records convert.
Unmapped fields (e.g. prompts and outputs) are never copied.
"""

import csv
import io
import json
from math import isfinite
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from evalgate.io.reader import InputError, _error, _parse, _read_text, _UniqueKeyLoader
from evalgate.io.writer import EvalRun
from evalgate.models.mapping import ImportMapping

_TRUE = {"true", "1", "yes", "pass", "passed"}
_FALSE = {"false", "0", "no", "fail", "failed"}
_MISSING = object()


class ImportSummary(NamedTuple):
    cases: int
    repeats: int
    ignored_fields: list[str]


def load_mapping(path: Path) -> ImportMapping:
    try:
        data = yaml.load(_read_text(path), Loader=_UniqueKeyLoader)  # SafeLoader subclass
    except yaml.YAMLError as exc:
        raise InputError([_error("mapping_yaml", f"{path}: invalid YAML: {exc}")]) from exc
    return _parse(ImportMapping, data, str(path))


def _read_records(path: Path, fmt: str) -> list[dict[str, Any]]:
    text = _read_text(path).removeprefix("﻿")  # Excel writes a BOM
    try:
        if fmt == "csv":
            return list(csv.DictReader(io.StringIO(text)))
        if fmt == "jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            rows = json.loads(text)
            if not isinstance(rows, list):
                raise ValueError("top level must be a list of objects")
    except (ValueError, csv.Error) as exc:
        raise InputError([_error("import_parse", f"{path}: cannot parse as {fmt}: {exc}")]) from exc
    bad = [i for i, r in enumerate(rows, 1) if not isinstance(r, dict)]
    if bad:
        raise InputError([_error("import_parse", f"{path}: record {bad[0]} is not an object")])
    return rows


def _get(record: dict[str, Any], key: str) -> Any:
    """Exact key first (CSV headers may contain dots), then a dotted path into nested JSON."""
    if key in record:
        return record[key]
    node: Any = record
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _blank(value: Any) -> bool:
    return value is _MISSING or value is None or (isinstance(value, str) and not value.strip())


def _number(value: Any) -> float:
    if isinstance(value, bool):  # bool is an int subclass; True is not a score
        raise ValueError(f"expected a number, got {value!r}")
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        raise ValueError(f"expected a number, got {value!r}") from None
    if not isfinite(number):
        raise ValueError(f"expected a finite number, got {value!r}")
    return number


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"expected true/false, got {value!r}")


def _convert(record: dict[str, Any], m: ImportMapping) -> dict[str, Any]:
    """One record -> EvalRun.record() arguments. Raises ValueError listing every issue."""
    problems: list[str] = []
    out: dict[str, Any] = {}

    def take(field: str, convert: Any, what: str) -> Any:
        value = _get(record, field)
        if _blank(value):
            return _MISSING
        try:
            return convert(value)
        except ValueError as exc:
            problems.append(f"{what} ({field!r}): {exc}")
            return _MISSING

    case_id = _get(record, m.case_id)
    if _blank(case_id):
        problems.append(f"case_id ({m.case_id!r}) is missing")
    out["case_id"] = str(case_id).strip()

    status = "ok"
    if m.status is not None:
        raw = _get(record, m.status)
        key = "" if _blank(raw) else str(raw).strip()
        status = m.status_values.get(key, key)
        if status not in ("ok", "error"):
            problems.append(
                f"status ({m.status!r}): unknown value {raw!r}; map it in status_values"
            )
    out["status"] = status

    if m.repeat is not None:
        raw = _get(record, m.repeat)
        if _blank(raw):
            problems.append(f"repeat ({m.repeat!r}) is missing")
        else:
            try:
                out["repeat"] = _repeat(raw)
            except ValueError as exc:
                problems.append(f"repeat ({m.repeat!r}): {exc}")

    for section, mapping, convert in (
        ("scores", m.scores, _number),
        ("metrics", m.metrics, _number),
        ("checks", m.checks, _bool),
    ):
        values = {name: take(f, convert, f"{section}.{name}") for name, f in mapping.items()}
        out[section] = {k: v for k, v in values.items() if v is not _MISSING}  # blank -> missing
    out["metadata"] = {
        name: v for name, f in m.metadata.items() if not _blank(v := _get(record, f))
    }
    if m.error is not None and not _blank(e := _get(record, m.error)):
        out["error"] = str(e)
    if problems:
        raise ValueError("; ".join(problems))
    return out


def _repeat(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"expected a non-negative integer, got {value!r}")
    number = _number(value)
    if number < 0 or number != int(number):
        raise ValueError(f"expected a non-negative integer, got {value!r}")
    return int(number)


def _referenced(m: ImportMapping) -> set[str]:
    fields = [m.case_id, m.repeat, m.status, m.error]
    fields += [*m.scores.values(), *m.metrics.values(), *m.checks.values(), *m.metadata.values()]
    return {f.split(".")[0] for f in fields if f} | {f for f in fields if f}


def import_results(
    source: Path,
    mapping: Path,
    out: Path,
    *,
    run_id: str | None = None,
    overwrite: bool = False,
) -> ImportSummary:
    """Write `source` as an EvalGate run directory at `out`. Raises InputError on any bad input."""
    m = load_mapping(mapping)
    fmt = m.format or source.suffix.lower().lstrip(".")
    if fmt not in ("csv", "json", "jsonl"):
        raise InputError(
            [
                _error(
                    "import_format",
                    f"{source}: unknown format {fmt!r}; set `format` in the mapping",
                )
            ]
        )
    run_id = run_id or m.manifest.run_id
    if not run_id:
        raise InputError(
            [_error("import_run_id", f"{mapping}: set manifest.run_id or pass --run-id")]
        )

    records = _read_records(source, fmt)
    if not records:
        raise InputError([_error("no_cases", f"{source}: contains no records")])
    rows, diagnostics = [], []
    for n, record in enumerate(records, 1):
        try:
            rows.append(_convert(record, m))
        except ValueError as exc:
            diagnostics.append(_error("import_record", f"{source}: record {n}: {exc}"))
    if diagnostics:
        raise InputError(diagnostics)

    repeats = max(r["repeat"] for r in rows) + 1 if m.repeat is not None else None
    manifest = m.manifest.model_dump(exclude={"run_id"})
    try:
        with EvalRun(
            run_id,
            dataset=manifest["dataset_id"],
            dataset_version=manifest["dataset_version"],
            evaluator_version=manifest["evaluator_version"],
            application=manifest["application"],
            application_version=manifest["application_version"],
            path=out,
            overwrite=overwrite,
            repeats=repeats,
        ) as run:
            for n, row in enumerate(rows, 1):
                kwargs = {k: row[k] for k in ("metadata", "repeat") if k in row}
                try:
                    if row["status"] == "error":
                        run.record_error(row["case_id"], row.get("error", "error"), **kwargs)
                    else:
                        run.record(
                            row["case_id"],
                            scores=row["scores"],
                            metrics=row["metrics"],
                            checks=row["checks"],
                            **kwargs,
                        )
                except ValueError as exc:  # duplicates, pydantic validation
                    diagnostics.append(_error("import_record", f"{source}: record {n}: {exc}"))
            if diagnostics:
                raise InputError(diagnostics)  # inside `with`: nothing is committed
    except FileExistsError as exc:
        raise InputError([_error("import_exists", str(exc))]) from exc

    seen = {k for r in records for k in r}
    return ImportSummary(
        cases=len({r["case_id"] for r in rows}),
        repeats=repeats or 1,
        ignored_fields=sorted(seen - _referenced(m)),
    )
