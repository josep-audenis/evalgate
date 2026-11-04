import os
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from evalgate.models import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_REPEATS,
    CaseResult,
    Report,
    RunManifest,
)
from evalgate.models.policy import POLICY_TEMPLATE


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json_report(report: Report, path: Path) -> None:
    _atomic_write(path, report.model_dump_json(indent=2) + "\n")


def write_html_report(report: Report, path: Path) -> None:
    from evalgate.reporting.html import render_html

    _atomic_write(path, render_html(report))


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
        **metadata: Any,
    ) -> None:
        clash = sorted(metadata.keys() & RunManifest.model_fields.keys())
        if clash:  # e.g. dataset_id= would silently override dataset=
            raise ValueError(f"metadata keys collide with manifest fields: {', '.join(clash)}")
        self.manifest = RunManifest.model_validate(
            {
                "schema_version": SCHEMA_VERSION if repeats is None else SCHEMA_VERSION_REPEATS,
                "run_id": run_id,
                "dataset_id": dataset,
                "dataset_version": dataset_version,
                "evaluator_version": evaluator_version,
                "application": application,
                "application_version": application_version,
                "repeats": repeats,
            }
            | metadata
        )
        self.path = Path(path) if path is not None else Path("evals") / run_id
        self.overwrite = overwrite
        self._partial = self.path / ".cases.jsonl.partial"
        self._file: IO[str] | None = None
        self._ids: set[tuple[str, int | None]] = set()

    def __enter__(self) -> Self:
        exists = (self.path / "run.json").exists() or (self.path / "cases.jsonl").exists()
        if exists and not self.overwrite:
            raise FileExistsError(f"{self.path} already holds a run; pass overwrite=True")
        self.path.mkdir(parents=True, exist_ok=True)
        self._ids = set()
        self._file = self._partial.open("w", encoding="utf-8")
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
            return  # the partial file is never loaded; any previous run stays intact
        # Old run.json goes first so old manifest + new cases can never load together.
        (self.path / "run.json").unlink(missing_ok=True)
        os.replace(self._partial, self.path / "cases.jsonl")
        # Plain runs keep the exact 1.0 manifest shape (no "repeats": null).
        skip = {"repeats"} if self.manifest.repeats is None else None
        manifest = self.manifest.model_dump_json(indent=2, exclude=skip)
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
    ) -> None:
        """Record a successful case. Values are validated now, not at compare time.

        With EvalRun(repeats=k), call once per repeat with repeat=0..k-1.
        """
        self._write(
            CaseResult.model_validate(
                {
                    "case_id": case_id,
                    "status": "ok",
                    "repeat": repeat,
                    "scores": scores or {},
                    "checks": checks or {},
                    "metrics": metrics or {},
                    "metadata": metadata or {},
                }
            )
        )

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
