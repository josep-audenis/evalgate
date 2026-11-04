"""Release policy (evalgate.yaml). Unknown keys are rejected so typos never weaken a policy."""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Direction = Literal["maximize", "minimize"]


class _Strict(BaseModel):
    # strict: `true` is not 1 and "10" is not 10; a policy value must mean what it says.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ComparisonRules(_Strict):
    require_same_cases: bool = True
    require_same_dataset: bool = True
    require_same_evaluator: bool = True
    # Cases `ok` in both runs needed for a decision; fewer -> INVALID. Unset: warn below 30.
    min_compared_cases: Annotated[int, Field(ge=1)] | None = None


class MetricRule(_Strict):
    """A compared metric. Regression = change in the worse direction of the matched-case mean."""

    path: Annotated[str, Field(pattern=r"^(scores|metrics)\.[^.]+$")]
    direction: Direction
    max_mean_regression_abs: NonNegative | None = None
    max_mean_regression_pct: NonNegative | None = None

    @model_validator(mode="after")
    def _has_threshold(self) -> Self:
        if self.max_mean_regression_abs is None and self.max_mean_regression_pct is None:
            raise ValueError(
                "set max_mean_regression_abs and/or max_mean_regression_pct; "
                "a metric without a threshold would never fail"
            )
        return self


class StatisticsRules(_Strict):
    # Confidence level of the interval reported for each metric's mean change.
    # 0.5..0.999: outside this range the interval is degenerate or numerically unstable.
    confidence: Annotated[float, Field(ge=0.5, le=0.999)] = 0.95
    # true: a metric rule passes only if the whole interval is within its threshold. This can
    # only turn a PASS into a FAIL, never the reverse.
    require_conclusive: bool = False


class CheckRules(_Strict):
    required: list[str] = []
    max_new_failures: Annotated[int, Field(ge=0)] | None = None
    # FAIL a check whose newly-failing excess is statistically significant (one-sided exact
    # McNemar, alpha = 1 - statistics.confidence). Too few flips can't reach significance,
    # so pair it with max_new_failures rather than relying on it alone.
    fail_on_significant_regression: bool = False


class Policy(_Strict):
    version: Literal[1]
    comparison: ComparisonRules = ComparisonRules()
    metrics: dict[str, MetricRule] = {}
    checks: CheckRules = CheckRules()
    statistics: StatisticsRules = StatisticsRules()
    # Candidate cases with `status: error`. Default 0: pipeline failures never pass silently.
    max_execution_errors: Annotated[int, Field(ge=0)] = 0

    @field_validator("version", mode="before")
    @classmethod
    def _version_not_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):  # Literal[1] would accept True
            raise ValueError("version must be the integer 1")
        return v

    @model_validator(mode="after")
    def _has_rules(self) -> Self:
        checks = self.checks
        if not (self.metrics or checks.required or checks.max_new_failures is not None):
            # fail_on_significant_regression alone doesn't count: with few flips it can never
            # fail (4 of 4 newly failing gives p=0.0625), so it would pass small regressions.
            raise ValueError("policy has no metrics or checks rules; it would pass anything")
        return self


# Written by `evalgate init`; a test keeps it loadable as a Policy.
POLICY_TEMPLATE = """\
# EvalGate release policy. Compares a candidate run against a baseline run.
version: 1

# Comparability requirements. Any mismatch makes the result INVALID (exit 2).
comparison:
  require_same_cases: true      # every case_id must exist in both runs
  require_same_dataset: true    # same dataset_id and dataset_version
  require_same_evaluator: true  # same evaluator_version
  # min_compared_cases: 30     # fewer cases ok in both runs -> INVALID.
                                # Unset: only a warning below 30 cases.

# Compared metrics. `path` is scores.<name> or metrics.<name> in cases.jsonl.
# `direction` is required: EvalGate never assumes higher is better.
# Regression = mean over matched cases, moved in the worse direction.
metrics:
  quality:
    path: scores.quality
    direction: maximize
    max_mean_regression_abs: 0.02   # absolute drop allowed

  latency:
    path: metrics.latency_ms
    direction: minimize
    max_mean_regression_pct: 10     # percent increase allowed; undefined if baseline mean is 0,
                                    # so also set max_mean_regression_abs for metrics that can be 0

checks:
  required:              # must pass in every candidate case that ran
    - schema_valid
  max_new_failures: 0    # checks that passed in baseline and fail in candidate
  # fail_on_significant_regression: true  # FAIL if a check's new failures significantly
  #                                       # exceed its new passes (exact McNemar test)

# Uncertainty: each metric change gets a confidence interval in the report.
statistics:
  confidence: 0.95
  require_conclusive: false   # true: a rule passes only if its whole CI is within the limit

# Candidate cases with status "error" (pipeline failures) allowed.
max_execution_errors: 0
"""
