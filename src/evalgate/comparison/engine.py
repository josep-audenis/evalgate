"""Paired baseline-vs-candidate comparison. Assumes inputs passed `validate_pair`."""

from collections import defaultdict
from statistics import fmean
from typing import Any

from evalgate.comparison.stats import mcnemar_p, mean_diff_ci
from evalgate.models import (
    CaseDelta,
    CaseResult,
    CategoryDelta,
    CheckChange,
    CheckStat,
    Comparison,
    ConfidenceInterval,
    MetricDelta,
    MetricRule,
    Run,
)


def _pct(x: float, base_mean: float) -> float | None:
    return x / abs(base_mean) * 100 if base_mean != 0 else None


def _worse(delta: float, rule: MetricRule) -> bool:
    return delta < 0 if rule.direction == "maximize" else delta > 0


def _value(case: CaseResult, rule: MetricRule) -> float:
    value = case.value(rule.path)
    if value is None:  # validate_pair guarantees presence
        raise ValueError(f"{case.case_id}: missing {rule.path}")
    return value


def _delta(values: list[tuple[float, float]], confidence: float) -> dict[str, Any]:
    """Paired mean change over (baseline, candidate) values, with its confidence interval."""
    base_mean = fmean(b for b, _ in values)
    delta = fmean(c for _, c in values) - base_mean
    low, high, method, resamples = mean_diff_ci([c - b for b, c in values], confidence)
    return {
        "cases": len(values),
        "baseline_mean": base_mean,
        "candidate_mean": fmean(c for _, c in values),
        "delta_abs": delta,
        "delta_pct": _pct(delta, base_mean),
        "ci": ConfidenceInterval(
            confidence=confidence,
            low=low,
            high=high,
            pct_low=_pct(low, base_mean),
            pct_high=_pct(high, base_mean),
            method=method,
            resamples=resamples,
        ),
    }


def compare(
    baseline: Run, candidate: Run, metrics: dict[str, MetricRule], confidence: float = 0.95
) -> Comparison:
    """Compare matched cases. Means and deltas use only cases `ok` in both runs."""
    base = {c.case_id: c for c in baseline.cases}
    cand = {c.case_id: c for c in candidate.cases}
    matched = sorted(base.keys() & cand.keys())
    pairs = [(base[i], cand[i]) for i in matched if base[i].status == cand[i].status == "ok"]

    metric_deltas: list[MetricDelta] = []
    regressed: list[CaseDelta] = []
    improved: list[CaseDelta] = []
    by_category: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for name, rule in sorted(metrics.items()):
        values = [(_value(b, rule), _value(c, rule)) for b, c in pairs]
        metric_deltas.append(
            MetricDelta(
                name=name, path=rule.path, direction=rule.direction, **_delta(values, confidence)
            )
        )
        for (b_case, _), (b, c) in zip(pairs, values, strict=True):
            if c != b:
                change = CaseDelta(
                    case_id=b_case.case_id, metric=name, baseline=b, candidate=c, delta_abs=c - b
                )
                (regressed if _worse(c - b, rule) else improved).append(change)
            category = b_case.metadata.get("category")
            if isinstance(category, str):
                by_category[(category, name)].append((b, c))

    # Largest change first within each metric; case_id breaks ties deterministically.
    def order(d: CaseDelta) -> tuple[str, float, str]:
        return (d.metric, -abs(d.delta_abs), d.case_id)

    newly_failing: list[CheckChange] = []
    newly_passing: list[CheckChange] = []
    # check -> [cases, baseline failures, candidate failures, newly failing, newly passing]
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    for b_case, c_case in pairs:
        for check in sorted(b_case.checks.keys() & c_case.checks.keys()):
            was, now = b_case.checks[check], c_case.checks[check]
            n = counts[check]
            n[0] += 1
            n[1] += not was
            n[2] += not now
            if was != now:
                flip = CheckChange(case_id=b_case.case_id, check=check)
                (newly_failing if was else newly_passing).append(flip)
                n[3 if was else 4] += 1

    return Comparison(
        matched_cases=len(matched),
        compared_cases=len(pairs),
        baseline_errors=[i for i in matched if base[i].status == "error"],
        candidate_errors=[i for i in sorted(cand) if cand[i].status == "error"],
        metrics=metric_deltas,
        regressed_cases=sorted(regressed, key=order),
        improved_cases=sorted(improved, key=order),
        newly_failing_checks=newly_failing,
        newly_passing_checks=newly_passing,
        check_stats=[
            CheckStat(
                check=check,
                cases=n[0],
                baseline_failures=n[1],
                candidate_failures=n[2],
                newly_failing=n[3],
                newly_passing=n[4],
                p_value=mcnemar_p(n[3], n[4]),
            )
            for check, n in sorted(counts.items())
        ],
        candidate_failing_checks=[
            CheckChange(case_id=i, check=check)
            for i in sorted(cand)
            if cand[i].status == "ok"
            for check, passed in sorted(cand[i].checks.items())
            if not passed
        ],
        categories=[
            CategoryDelta(category=category, metric=metric, **_delta(values, confidence))
            for (category, metric), values in sorted(by_category.items())
        ],
    )
