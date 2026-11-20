import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from pydantic import BaseModel, JsonValue, TypeAdapter

from evalgate.evaluators.core import Evaluator, apply
from evalgate.models import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_EVALUATORS,
    SCHEMA_VERSION_REPEATS,
    CaseResult,
    Report,
    RunManifest,
)
from evalgate.models.policy import POLICY_TEMPLATE


def _atomic_write(path: Path, text: str) -> None:
    # Unique temp name per process, so concurrent writers never share (and garble) a temp file.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")  # LF on every OS: same bytes
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Like _atomic_write, for bytes written exactly as given (report plugins)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json_report(report: BaseModel, path: Path) -> None:
    """Any report model (Report, CalibrationReport) as stable, indented JSON."""
    _atomic_write(path, report.model_dump_json(indent=2) + "\n")


def write_html_report(report: Report, path: Path) -> None:
    from evalgate.reporting.html import render_html

    _atomic_write(path, render_html(report))


def write_markdown_report(report: Report, path: Path) -> None:
    from evalgate.reporting.markdown import render_markdown

    _atomic_write(path, render_markdown(report))


_MISSING: Any = object()  # `output=None` is a valid JSON output, so absence needs a sentinel
_JSON = TypeAdapter(JsonValue)


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return True


def _stored_output(output: Any) -> JsonValue:
    """A validated, independent copy of the output, taken before evaluators can touch it."""
    try:
        value = _JSON.validate_python(output)
    except ValueError:
        # `from None`: pydantic's error repeats the raw output, which shouldn't reach logs.
        raise ValueError("output must be JSON-serializable to store it") from None
    if not _finite(value):
        raise ValueError("output contains NaN or infinity; it can't be stored as JSON")
    return value


def write_policy_template(path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite")
    _atomic_write(path, POLICY_TEMPLATE)


class EvalRun:
    """Record case results into a run directory (`run.json` + `cases.jsonl`).

    Cases stream to a partial file; only a clean exit from the `with` block publishes
    them and writes `run.json` last. An interrupted run therefore never loads as complete,
    and with `overwrite=True` a crash leaves the previous run untouched.
    """

    def __init__(
        self,
        run_id: str,
        *,
        dataset: str,
        dataset_version: str,
        evaluator_version: str,
        path: str | Path | None = None,
        application: str | None = None,
        application_version: str | None = None,
        overwrite: bool = False,
        repeats: int | None = None,
        evaluators: Sequence[Evaluator] | None = None,
        store_output: bool = False,
        **metadata: Any,
    ) -> None:
        clash = sorted(metadata.keys() & RunManifest.model_fields.keys())
        if clash:  # e.g. dataset_id= would silently override dataset=
            raise ValueError(f"metadata keys collide with manifest fields: {', '.join(clash)}")
        if not isinstance(store_output, bool):  # a truthy "no" must not store raw outputs
            raise TypeError("store_output must be a bool")
        # Private and immutable: evaluators added later would run without being declared.
        self._evaluators = tuple(evaluators or ())
        if any(not isinstance(ev, Evaluator) for ev in self._evaluators):
            raise TypeError("evaluators must be Evaluator instances (see evalgate.evaluators)")
        names = [ev.name for ev in self._evaluators]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"duplicate evaluator names: {', '.join(dupes)}")
        self._store_output = store_output
        # Lowest schema version covering the features used (1.2 is a superset of 1.1).
        if self._evaluators:
            schema_version = SCHEMA_VERSION_EVALUATORS
        elif repeats is not None:
            schema_version = SCHEMA_VERSION_REPEATS
        else:
            schema_version = SCHEMA_VERSION
        declared = {ev.name: ev.version for ev in sorted(self._evaluators, key=lambda e: e.name)}
        self.manifest = RunManifest.model_validate(
            {
                "schema_version": schema_version,
                "run_id": run_id,
                "dataset_id": dataset,
                "dataset_version": dataset_version,
                "evaluator_version": evaluator_version,
                "application": application,
                "application_version": application_version,
                "repeats": repeats,
                "evaluators": declared or None,
            }
            | metadata
        )
        self.path = Path(path) if path is not None else Path("evals") / run_id
        self.overwrite = overwrite
        self._partial = self.path / ".cases.jsonl.partial"
        self._file: IO[str] | None = None
        self._ids: set[tuple[str, int | None]] = set()
        self.evaluator_errors = 0

    def __enter__(self) -> Self:
        exists = (self.path / "run.json").exists() or (self.path / "cases.jsonl").exists()
        if exists and not self.overwrite:
            raise FileExistsError(f"{self.path} already holds a run; pass overwrite=True")
        self.path.mkdir(parents=True, exist_ok=True)
        self._ids = set()
        self.evaluator_errors = 0  # rows (case x repeat) recorded as errors by a failing evaluator
        self._file = self._partial.open("w", encoding="utf-8", newline="\n")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._file is not None
        self._file.close()
        self._file = None
        if exc_type is not None:
            self._partial.unlink(missing_ok=True)  # never loaded; any previous run stays intact
            return
        # Old run.json goes first so old manifest + new cases can never load together.
        (self.path / "run.json").unlink(missing_ok=True)
        os.replace(self._partial, self.path / "cases.jsonl")
        manifest = self.manifest.model_dump_json(indent=2)
        _atomic_write(self.path / "run.json", manifest + "\n")

    def _write(self, case: CaseResult) -> None:
        if self._file is None:
            raise RuntimeError("EvalRun.record() must be called inside `with EvalRun(...)`")
        k = self.manifest.repeats
        if k is None and case.repeat is not None:
            raise ValueError("pass repeats=k to EvalRun before recording repeat=")
        if k is not None and (case.repeat is None or case.repeat >= k):
            raise ValueError(f"{case.case_id}: repeat must be 0..{k - 1} (EvalRun repeats={k})")
        key = (case.case_id, case.repeat)
        if key in self._ids:
            what = "case_id" if case.repeat is None else f"repeat {case.repeat} of case_id"
            raise ValueError(f"duplicate {what}: {case.case_id}")
        self._ids.add(key)
        self._file.write(case.model_dump_json(exclude_defaults=True) + "\n")
        self._file.flush()

    def record(
        self,
        case_id: str,
        *,
        scores: dict[str, float] | None = None,
        checks: dict[str, bool] | None = None,
        metrics: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
        repeat: int | None = None,
        output: Any = _MISSING,
        case: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a successful case. Values are validated now, not at compare time.

        With EvalRun(repeats=k), call once per repeat with repeat=0..k-1.
        With EvalRun(evaluators=...), pass the application's `output` (and optionally the
        dataset record as `case`); their results join `scores` / `checks`. `case` is never
        written, and `output` only with EvalRun(store_output=True); a stored `None` output is
        omitted from the file, like any unset field.

        An evaluator that raises or returns the wrong type does not raise here: the case is
        recorded as `status: error` with the message `evaluator <name> failed`.
        """
        if output is _MISSING and (self._evaluators or self._store_output):
            raise ValueError(f"{case_id}: output= is required with evaluators or store_output")
        # Validate the caller's own values first, so they are checked even if an evaluator fails.
        result = CaseResult.model_validate(
            {
                "case_id": case_id,
                "status": "ok",
                "repeat": repeat,
                "scores": scores or {},
                "checks": checks or {},
                "metrics": metrics or {},
                "metadata": metadata or {},
                "output": _stored_output(output) if self._store_output else None,
            }
        )
        if self._evaluators:
            clash = sorted(
                {e.name for e in self._evaluators if e.kind == "score"} & result.scores.keys()
                | {e.name for e in self._evaluators if e.kind == "check"} & result.checks.keys()
            )
            if clash:
                raise ValueError(f"{case_id}: evaluator names clash with values passed: {clash}")
            applied = apply(self._evaluators, output, case or {})
            if applied.failed is not None:
                # Fixed text: an evaluator's exception can quote the output. Never stores output.
                self.evaluator_errors += 1
                self.record_error(
                    case_id, f"evaluator {applied.failed} failed", metadata=metadata, repeat=repeat
                )
                return
            result.scores |= applied.scores
            result.checks |= applied.checks
        self._write(result)

    def record_error(
        self,
        case_id: str,
        error: str,
        *,
        metadata: dict[str, Any] | None = None,
        repeat: int | None = None,
    ) -> None:
        """Record that the pipeline failed on a case; never invent placeholder scores."""
        self._write(
            CaseResult.model_validate(
                {
                    "case_id": case_id,
                    "status": "error",
                    "repeat": repeat,
                    "error": error,
                    "metadata": metadata or {},
                }
            )
        )
