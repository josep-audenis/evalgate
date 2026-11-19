"""Informational ranking of candidates on one policy metric (docs/design/phase-7.md §3). Pure.

No aggregate score and no weighting: one metric, its declared direction, ties in command-line
order. It never changes a status, the exit code or a report.
"""

from collections.abc import Sequence

from evalgate.models import Report
from evalgate.models.multi import RankItem


def exit_code(reports: Sequence[Report]) -> int:
    """2 if any candidate is INVALID, else 1 if any FAILs, else 0 (fail closed)."""
    statuses = {r.status for r in reports}
    return 2 if "INVALID" in statuses else 1 if "FAIL" in statuses else 0


def rank(reports: Sequence[Report], rank_by: str) -> list[RankItem]:
    """Non-INVALID candidates that have metric `rank_by`, best first by its candidate mean."""
    found = []
    for n, report in enumerate(reports, 1):
        if report.status == "INVALID" or report.comparison is None:
            continue
        metric = next((m for m in report.comparison.metrics if m.name == rank_by), None)
        if metric is not None:
            found.append((n, report, metric))
    # One policy, so one direction. sorted() is stable: ties keep command-line order.
    found.sort(
        key=lambda x: -x[2].candidate_mean if x[2].direction == "maximize" else x[2].candidate_mean
    )
    return [
        RankItem(
            rank=i,
            n=n,
            run_id=report.candidate.run_id if report.candidate else None,
            status=report.status,
            value=metric.candidate_mean,
            cases=metric.cases,
        )
        for i, (n, report, metric) in enumerate(found, 1)
    ]
