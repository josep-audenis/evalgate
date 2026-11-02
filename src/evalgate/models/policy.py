"""Release policy (evalgate.yaml). Unknown keys are rejected so typos never weaken a policy."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Direction = Literal["maximize", "minimize"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ComparisonRules(_Strict):
    require_same_cases: bool = True
    require_same_dataset: bool = True
    require_same_evaluator: bool = True


class MetricRule(_Strict):
    """A compared metric. Regression = change in the worse direction of the matched-case mean."""

    path: Annotated[str, Field(pattern=r"^(scores|metrics)\.[^.]+$")]
    direction: Direction
    max_mean_regression_abs: NonNegative | None = None
    max_mean_regression_pct: NonNegative | None = None


class CheckRules(_Strict):
    required: list[str] = []
    max_new_failures: Annotated[int, Field(ge=0)] | None = None


class Policy(_Strict):
    version: Literal[1]
    comparison: ComparisonRules = ComparisonRules()
    metrics: dict[str, MetricRule] = {}
    checks: CheckRules = CheckRules()
    # Candidate cases with `status: error`. Default 0: pipeline failures never pass silently.
    max_execution_errors: Annotated[int, Field(ge=0)] = 0
