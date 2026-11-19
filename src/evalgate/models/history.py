"""Local history store entries (index.jsonl): summaries of compares and runs, never outputs.

See docs/design/phase-7.md §2. Every entry is strict so a corrupt or hand-edited line is an
error (`history_parse`), never silently misread.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from evalgate.models.policy import Direction
from evalgate.models.report import Status
from evalgate.models.run import NonEmptyStr, Number

HISTORY_VERSION: Literal[2] = 2  # written; 1 and 2 are read
MAX_LABEL = 200

Count = Annotated[int, Field(ge=0)]


def _real_time(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")  # rejects 2026-13-45T99:99:99Z
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Identity(_Strict):
    """Who was evaluated: the identity fields of a run.json (extra manifest fields excluded)."""

    run_id: NonEmptyStr
    application: str | None = None
    application_version: str | None = None
    dataset_id: NonEmptyStr
    dataset_version: NonEmptyStr


class FailingCheck(_Strict):
    case_id: str
    check: str


class MetricRow(_Strict):
    """One policy metric of a compare, over its compared cases."""

    path: str
    direction: Direction
    baseline_mean: Number
    candidate_mean: Number
    delta_abs: Number
    cases: Count


class Stat(_Strict):
    """A mean (scores/metrics) or pass rate (checks) over the ok cases that have the value."""

    value: Number
    cases: Annotated[int, Field(ge=1)]


class _Entry(_Strict):
    history_version: Literal[1, 2]
    id: Annotated[int, Field(ge=1)]
    # Metadata only: listings are in id order, nothing sorts or decides on time.
    recorded_at: Annotated[
        str,
        Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
        AfterValidator(_real_time),
    ]
    label: Annotated[str, Field(max_length=MAX_LABEL)] | None = None


class CompareEntry(_Entry):
    kind: Literal["compare"]
    status: Status
    baseline: Identity | None  # None when that run didn't load
    candidate: Identity | None
    metrics: list[MetricRow]  # one per distinct policy metric path; empty when INVALID
    # Candidate, compared cases only (v2: from failure_groups; v1 entries may include
    # candidate-only cases when require_same_cases was false).
    failing_checks: list[FailingCheck]
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RunEntry(_Entry):
    kind: Literal["run"]
    run: Identity
    cases: Count  # after collapsing repeats
    errors: Count
    means: dict[str, Stat]  # "scores.x" / "metrics.y"
    checks: dict[str, Stat]  # pass rates
    failing_checks: list[FailingCheck]
    # Since v2: the ok cases after collapsing repeats (for `history failures`' `of`).
    case_ids: list[str] | None = None

    @model_validator(mode="after")
    def _case_ids_by_version(self) -> Self:
        if self.history_version == 1 and self.case_ids is not None:
            raise ValueError("case_ids is only allowed on history_version 2 entries")
        if self.history_version == 2 and self.case_ids is None:
            raise ValueError("case_ids is required on history_version 2 run entries")
        if self.history_version == 2 and self.case_ids is not None:
            # What EvalGate writes, so `failing <= of` holds; a hand edit can't break it silently.
            if self.case_ids != sorted(set(self.case_ids)):
                raise ValueError("case_ids must be sorted and unique")
            if not {f.case_id for f in self.failing_checks} <= set(self.case_ids):
                raise ValueError("every failing case must be in case_ids")
            pairs = [(f.case_id, f.check) for f in self.failing_checks]
            if len(pairs) != len(set(pairs)):
                raise ValueError("failing_checks has a duplicate (case_id, check) pair")
        return self


HistoryEntry = Annotated[CompareEntry | RunEntry, Field(discriminator="kind")]
