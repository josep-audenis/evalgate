"""Path-based entry point shared by the CLI and the SDK, so both produce identical reports."""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evalgate._version import __version__
from evalgate.comparison.ranking import exit_code, rank
from evalgate.engine import evaluate, invalid_report
from evalgate.io import InputError, load_policy, load_run
from evalgate.models import Diagnostic, Policy, Report, Run
from evalgate.models.multi import CandidateStatus, MultiSummary, RankItem


def compare(
    baseline: str | Path,
    candidate: str | Path,
    policy: str | Path = "evalgate.yaml",
) -> Report:
    """Load two run directories and a policy, then decide. Bad input yields an INVALID report."""
    diagnostics: list[Diagnostic] = []
    runs: list[Run] = []
    loaded_policy: Policy | None = None
    for run_dir in (baseline, candidate):
        try:
            runs.append(load_run(Path(run_dir)))
        except InputError as exc:
            diagnostics += exc.diagnostics
    try:
        loaded_policy = load_policy(Path(policy))
    except InputError as exc:
        diagnostics += exc.diagnostics

    if diagnostics or loaded_policy is None:
        return invalid_report(diagnostics)
    return evaluate(runs[0], runs[1], loaded_policy)


@dataclass(frozen=True)
class MultiResult:
    """Each candidate judged against one baseline (docs/design/phase-7.md §3)."""

    reports: list[Report]  # in the order the candidates were given
    exit_code: int  # 2 if any INVALID, else 1 if any FAIL, else 0
    ranking: list[RankItem] | None  # None without rank_by
    rank_by: str | None = None

    def summary(self) -> MultiSummary:
        baseline = next((r.baseline for r in self.reports if r.baseline), None)
        return MultiSummary(
            evalgate_version=__version__,
            baseline_run_id=baseline.run_id if baseline else None,
            exit_code=self.exit_code,
            rank_by=self.rank_by,
            candidates=[
                CandidateStatus(
                    n=n, run_id=r.candidate.run_id if r.candidate else None, status=r.status
                )
                for n, r in enumerate(self.reports, 1)
            ],
            ranking=self.ranking,
            cases_differ=len({i.cases for i in self.ranking or []}) > 1,
        )


def compare_many(
    baseline: str | Path,
    candidates: Sequence[str | Path],
    policy: str | Path = "evalgate.yaml",
    *,
    rank_by: str | None = None,
) -> MultiResult:
    """Judge each candidate against `baseline` with the same engine as `compare()`.

    Raises TypeError if `candidates` is a single path, ValueError if it's empty or if `rank_by`
    names no metric of a policy that loads. Bad input otherwise gives INVALID reports.
    """
    # A sequence keeps command-line order: a set (hash order) would make `n` nondeterministic.
    if isinstance(candidates, str | bytes | os.PathLike) or not isinstance(candidates, Sequence):
        raise TypeError("candidates must be a list or tuple of paths")
    paths = list(candidates)
    if not paths:
        raise ValueError("give at least one candidate")
    if rank_by is not None:
        try:
            names = load_policy(Path(policy)).metrics
        except InputError:
            names = None  # every report will be INVALID; nothing to rank
        if names is not None and rank_by not in names:
            raise ValueError(f"rank_by {rank_by!r} is not a policy metric: {sorted(names)}")
    # shortcut: re-reads the baseline and policy per candidate, so each report is exactly a
    # two-run compare; load once if N gets large.
    reports = [compare(baseline, c, policy) for c in paths]
    return MultiResult(
        reports=reports,
        exit_code=exit_code(reports),
        ranking=rank(reports, rank_by) if rank_by is not None else None,
        rank_by=rank_by,
    )
