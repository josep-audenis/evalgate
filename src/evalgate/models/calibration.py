"""Human labels (labels.jsonl) and the calibration report (`evalgate calibrate`)."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from evalgate.models.run import NonEmptyStr, Number


class LabelLine(BaseModel):
    """One labelled case: the human's value for some of its checks and/or scores."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: NonEmptyStr
    checks: dict[NonEmptyStr, StrictBool] = {}
    scores: dict[NonEmptyStr, Number] = {}

    @model_validator(mode="after")
    def _labels_something(self) -> Self:
        if not self.checks and not self.scores:
            raise ValueError("give at least one label in `checks` or `scores`")
        return self


class CalibrationEntry(BaseModel):
    """Agreement between one check or score and its human labels.

    Units: `n` counts (case, repeat) rows with a value and `n_cases` their distinct case ids.
    `errors` counts labelled rows with status error (excluded); `missing` counts labelled cases
    that gave neither a value nor an error. Figures are null when undefined: all when n is 0,
    `kappa` when expected agreement is 1, `spearman` when n < 2 or either side is constant.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["check", "score"]
    name: str
    n: int
    n_cases: int
    errors: int
    missing: int
    accuracy: float | None = None  # checks
    kappa: float | None = None  # checks: Cohen's kappa
    mae: float | None = None  # scores: mean absolute error, in the score's units
    spearman: float | None = None  # scores: Pearson correlation of average ranks


class CalibrationReport(BaseModel):
    """Reporting only: calibration never makes a decision and never feeds a policy."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    labelled_cases: int
    entries: list[CalibrationEntry]
