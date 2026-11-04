"""Import mapping (mapping.yaml): how a foreign results file becomes an EvalGate run.

Values are source field names. Unknown keys are rejected so a typo never drops data silently.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

FieldName = str  # a column (CSV) or key (JSON); JSON keys may be dotted paths like "a.b.c"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ImportManifest(_Strict):
    run_id: str | None = Field(default=None, min_length=1)  # or pass --run-id
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    application: str | None = None
    application_version: str | None = None  # quote it in YAML: 1.4 is a number


class ImportMapping(_Strict):
    version: Literal[1]
    format: Literal["csv", "json", "jsonl"] | None = None  # None: from the file extension
    manifest: ImportManifest
    case_id: FieldName
    repeat: FieldName | None = None
    status: FieldName | None = None
    # Source status value -> "ok" / "error". Without it, values must already be ok/error.
    status_values: dict[str, Literal["ok", "error"]] = {}
    error: FieldName | None = None
    scores: dict[str, FieldName] = {}
    metrics: dict[str, FieldName] = {}
    checks: dict[str, FieldName] = {}
    metadata: dict[str, FieldName] = {}

    @model_validator(mode="after")
    def _maps_something(self) -> Self:
        if not (self.scores or self.metrics or self.checks):
            raise ValueError("map at least one of scores, metrics or checks")
        if self.status_values and self.status is None:
            raise ValueError("status_values needs a status field")
        return self
