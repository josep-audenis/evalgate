import os
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Self

from evalgate.models import SCHEMA_VERSION, CaseResult, Report, RunManifest
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
        **metadata: Any,
    ) -> None:
        clash = sorted(metadata.keys() & RunManifest.model_fields.keys())
        if clash:  # e.g. dataset_id= would silently override dataset=
            raise ValueError(f"metadata keys collide with manifest fields: {', '.join(clash)}")
        self.manifest = RunManifest.model_validate(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "dataset_id": dataset,
                "dataset_version": dataset_version,
                "evaluator_version": evaluator_version,
                "application": application,
                "application_version": application_version,
            }
            | metadata
        )
        self.path = Path(path) if path is not None else Path("evals") / run_id
        self.overwrite = overwrite
        self._partial = self.path / ".cases.jsonl.partial"
        self._file: IO[str] | None = None
        self._ids: set[str] = set()

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
        _atomic_write(self.path / "run.json", self.manifest.model_dump_json(indent=2) + "\n")

    def _write(self, case: CaseResult) -> None:
        if self._file is None:
            raise RuntimeError("EvalRun.record() must be called inside `with EvalRun(...)`")
        if case.case_id in self._ids:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        self._ids.add(case.case_id)
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
    ) -> None:
        """Record a successful case. Values are validated now, not at compare time."""
        self._write(
            CaseResult.model_validate(
                {
                    "case_id": case_id,
                    "status": "ok",
                    "scores": scores or {},
                    "checks": checks or {},
                    "metrics": metrics or {},
                    "metadata": metadata or {},
                }
            )
        )

    def record_error(
        self, case_id: str, error: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        """Record that the pipeline failed on a case; never invent placeholder scores."""
        self._write(
            CaseResult.model_validate(
                {"case_id": case_id, "status": "error", "error": error, "metadata": metadata or {}}
            )
        )
