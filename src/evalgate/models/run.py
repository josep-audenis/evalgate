"""Evaluation run artifacts: manifest (run.json) and case results (cases.jsonl)."""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    model_serializer,
)

SCHEMA_VERSION = "1.0"
# 1.1 adds repeated runs (`repeat` per case, `repeats` in the manifest); 1.0 files still load.
SCHEMA_VERSION_REPEATS = "1.1"

# Strict: rejects bools and numeric strings; finite only, so NaN/inf never reach a mean.
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonEmptyStr = Annotated[str, Field(min_length=1)]


def _unwrap_numpy_bool(v: Any) -> Any:
    # numpy comparisons yield np.bool_, which StrictBool rejects; unwrap only real bool scalars.
    dtype = getattr(v, "dtype", None)
    if dtype is not None and getattr(dtype, "kind", None) == "b" and getattr(v, "shape", ()) == ():
        return bool(v)
    return v


Check = Annotated[StrictBool, BeforeValidator(_unwrap_numpy_bool)]


class RunManifest(BaseModel):
    """Identity of one evaluated application version. Extra fields are kept as metadata."""

    model_config = ConfigDict(extra="allow", frozen=True)

    schema_version: Literal["1.0", "1.1"]
    run_id: NonEmptyStr
    dataset_id: NonEmptyStr
    dataset_version: NonEmptyStr
    evaluator_version: NonEmptyStr
    application: str | None = None
    application_version: str | None = None
    # Expected repeats per case (schema 1.1). Unset: inferred from the cases.
    repeats: Annotated[int, Field(strict=True, ge=1)] | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_repeats(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Plain runs serialize exactly as before schema 1.1 (run.json and report manifests).
        data: dict[str, Any] = handler(self)
        if self.repeats is None:
            data.pop("repeats", None)
        return data


class CaseResult(BaseModel):
    """Result of one test case. `status: error` means the pipeline failed on this case."""

    model_config = ConfigDict(extra="allow")

    case_id: NonEmptyStr
    status: Literal["ok", "error"]
    # Which evaluation of this case, 0..k-1 (schema 1.1). Unset: the case was run once.
    repeat: Annotated[int, Field(strict=True, ge=0)] | None = None
    scores: dict[str, Number] = {}
    checks: dict[str, Check] = {}
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
