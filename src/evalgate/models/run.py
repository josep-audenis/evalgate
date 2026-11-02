"""Evaluation run artifacts: manifest (run.json) and case results (cases.jsonl)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

SCHEMA_VERSION = "1.0"

# Strict: rejects bools and numeric strings; finite only, so NaN/inf never reach a mean.
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class RunManifest(BaseModel):
    """Identity of one evaluated application version. Extra fields are kept as metadata."""

    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: Literal["1.0"]
    run_id: NonEmptyStr
    dataset_id: NonEmptyStr
    dataset_version: NonEmptyStr
    evaluator_version: NonEmptyStr
    application: str | None = None
    application_version: str | None = None


class CaseResult(BaseModel):
    """Result of one test case. `status: error` means the pipeline failed on this case."""

    model_config = ConfigDict(extra="allow")

    case_id: NonEmptyStr
    status: Literal["ok", "error"]
    scores: dict[str, Number] = {}
    checks: dict[str, StrictBool] = {}
    metrics: dict[str, Number] = {}
    metadata: dict[str, Any] = {}
    error: str | None = None

    def value(self, path: str) -> float | None:
        """Resolve a metric path such as `scores.quality` or `metrics.latency_ms`."""
        section, _, key = path.partition(".")
        values: dict[str, float] = self.scores if section == "scores" else self.metrics
        return values.get(key)


class Run(BaseModel):
    manifest: RunManifest
    cases: list[CaseResult]
