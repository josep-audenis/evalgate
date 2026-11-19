"""More than two candidates: ranking items and the `--summary` JSON (docs/design/phase-7.md §3).

Informational only: nothing here changes a status, an exit code or a report.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from evalgate.models.report import Status


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class CandidateStatus(_Frozen):
    n: int  # 1-based position on the command line
    run_id: str | None  # None when the candidate didn't load
    status: Status


class RankItem(_Frozen):
    rank: int  # position, 1..k; ties keep command-line order (no shared ranks)
    n: int
    run_id: str | None
    status: Status
    value: float  # the candidate mean of the ranked metric
    cases: int  # compared cases behind that mean


class MultiSummary(_Frozen):
    summary_version: Literal[1] = 1
    evalgate_version: str
    baseline_run_id: str | None
    exit_code: int  # the decision's: 2 if any INVALID, else 1 if any FAIL, else 0
    rank_by: str | None
    candidates: list[CandidateStatus]
    ranking: list[RankItem] | None  # None without rank_by; [] when nothing could be ranked
    # Ranked means over different compared-case counts aren't directly comparable.
    cases_differ: bool
