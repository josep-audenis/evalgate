"""Local history store entries (index.jsonl): summaries of compares and runs, never outputs.

See docs/design/phase-7.md §2. Every entry is strict so a corrupt or hand-edited line is an
error (`history_parse`), never silently misread.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from evalgate.models.policy import Direction
from evalgate.models.report import Status
from evalgate.models.run import Number

HISTORY_VERSION: Literal[1] = 1
MAX_LABEL = 200

Count = Annotated[int, Field(ge=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Identity(_Strict):
    """Who was evaluated: the identity fields of a run.json (extra manifest fields excluded)."""

    run_id: str
    application: str | None = None
    application_version: str | None = None
    dataset_id: str
    dataset_version: str


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
    history_version: Literal[1]
    id: Annotated[int, Field(ge=1)]
    # Metadata only: listings are in id order, nothing sorts or decides on time.
    recorded_at: Annotated[str, Field(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")]
    label: Annotated[str, Field(max_length=MAX_LABEL)] | None = None


class CompareEntry(_Entry):
    kind: Literal["compare"]
    status: Status
    baseline: Identity | None  # None when that run didn't load
    candidate: Identity | None
    metrics: list[MetricRow]  # one per distinct policy metric path; empty when INVALID
    failing_checks: list[FailingCheck]  # candidate, compared cases only
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RunEntry(_Entry):
    kind: Literal["run"]
    run: Identity
    cases: Count  # after collapsing repeats
    errors: Count
    means: dict[str, Stat]  # "scores.x" / "metrics.y"
    checks: dict[str, Stat]  # pass rates
    failing_checks: list[FailingCheck]


HistoryEntry = Annotated[CompareEntry | RunEntry, Field(discriminator="kind")]
