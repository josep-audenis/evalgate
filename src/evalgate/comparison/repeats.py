"""Repeated runs: combine each case's repeats into one case. See docs/design/repeated-runs.md.

The case stays the unit of analysis, so repeats never inflate the sample size (SPEC 9.2).
"""

from collections import defaultdict
from statistics import fmean, stdev

from evalgate.models import (
    CaseResult,
    MetricRule,
    RepeatCheck,
    RepeatMetric,
    RepeatSummary,
    Run,
)


def repeats_per_case(run: Run) -> int:
    """k for a validated run (1 when cases carry no `repeat`)."""
    return len(run.cases) // len({c.case_id for c in run.cases})


def _group(run: Run) -> dict[str, list[CaseResult]]:
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for c in run.cases:
        groups[c.case_id].append(c)
    for reps in groups.values():
        reps.sort(key=lambda c: c.repeat or 0)
    return groups


def _mean_of_shared(values: list[dict[str, float]]) -> dict[str, float]:
    # Only keys present in every repeat: a partly missing metric must surface as metric_missing.
    shared = set(values[0]).intersection(*values[1:])
    return {k: fmean(v[k] for v in values) for k in sorted(shared)}


def collapse(run: Run) -> Run:
    """One case per case_id: metric means, strict checks, any errored repeat -> error.

    May raise OverflowError on values near the float limit; callers treat that as INVALID.
    """
    if repeats_per_case(run) == 1:
        return run
    cases = []
    for case_id, reps in _group(run).items():
        first = reps[0]
        errors = [r for r in reps if r.status == "error"]
        if errors:
            cases.append(
                CaseResult(
                    case_id=case_id,
                    status="error",
                    error=f"repeat {errors[0].repeat}: {errors[0].error or 'error'}",
                    metadata=first.metadata,
                )
            )
            continue
        shared_checks = set(first.checks).intersection(*(r.checks for r in reps[1:]))
        cases.append(
            CaseResult(
                case_id=case_id,
                status="ok",
                scores=_mean_of_shared([r.scores for r in reps]),
                metrics=_mean_of_shared([r.metrics for r in reps]),
                checks={k: all(r.checks[k] for r in reps) for k in sorted(shared_checks)},
                metadata=first.metadata,
            )
        )
    return Run(manifest=run.manifest, cases=cases)


def repeat_summary(
    baseline: Run, candidate: Run, compared: list[str], metrics: dict[str, MetricRule]
) -> RepeatSummary | None:
    """Pass rates and within-case spread over the compared cases; None if neither run repeats."""
    kb, kc = repeats_per_case(baseline), repeats_per_case(candidate)
    if kb == kc == 1:
        return None
    base, cand = _group(baseline), _group(candidate)

    def pass_rate(groups: dict[str, list[CaseResult]], check: str) -> float:
        return fmean(fmean(r.checks[check] for r in groups[i]) for i in compared)

    def spread(groups: dict[str, list[CaseResult]], k: int, rule: MetricRule) -> float | None:
        if k < 2:
            return None
        sds = []
        for i in compared:
            values = [r.value(rule.path) for r in groups[i]]
            if None in values:  # unreachable: validation requires the same fields in every repeat
                raise RuntimeError(f"internal error: {i} missing {rule.path} in a repeat")
            sds.append(stdev(v for v in values if v is not None))
        return fmean(sds)

    # Checks present in every repeat of every compared case, in both runs (as in check_stats).
    checks = sorted(
        set.intersection(*(set(r.checks) for g in (base, cand) for i in compared for r in g[i]))
        if compared
        else set()
    )
    return RepeatSummary(
        baseline_repeats=kb,
        candidate_repeats=kc,
        checks=[
            RepeatCheck(
                check=c,
                baseline_pass_rate=pass_rate(base, c),
                candidate_pass_rate=pass_rate(cand, c),
            )
            for c in checks
        ],
        metrics=[
            RepeatMetric(
                name=name, baseline_sd=spread(base, kb, rule), candidate_sd=spread(cand, kc, rule)
            )
            for name, rule in sorted(metrics.items())
        ]
        if compared
        else [],
    )
