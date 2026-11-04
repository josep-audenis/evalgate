"""Paired baseline-vs-candidate comparison. Assumes inputs passed `validate_pair`."""

from collections import defaultdict
from statistics import fmean

from evalgate.comparison.stats import mean_diff_ci
from evalgate.models import (
    CaseDelta,
    CaseResult,
    CategoryDelta,
    CheckChange,
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
        base_mean = fmean(b for b, _ in values)
        cand_mean = fmean(c for _, c in values)
        delta = cand_mean - base_mean
        low, high, method, resamples = mean_diff_ci([c - b for b, c in values], confidence)
        metric_deltas.append(
            MetricDelta(
                name=name,
                path=rule.path,
                direction=rule.direction,
                cases=len(values),
                baseline_mean=base_mean,
                candidate_mean=cand_mean,
                delta_abs=delta,
                delta_pct=_pct(delta, base_mean),
                ci=ConfidenceInterval(
                    confidence=confidence,
                    low=low,
                    high=high,
                    pct_low=_pct(low, base_mean),
                    pct_high=_pct(high, base_mean),
                    method=method,
                    resamples=resamples,
                ),
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
    for b_case, c_case in pairs:
        for check in sorted(b_case.checks.keys() & c_case.checks.keys()):
            was, now = b_case.checks[check], c_case.checks[check]
            if was != now:
                flip = CheckChange(case_id=b_case.case_id, check=check)
                (newly_failing if was else newly_passing).append(flip)

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
        candidate_failing_checks=[
            CheckChange(case_id=i, check=check)
            for i in sorted(cand)
            if cand[i].status == "ok"
            for check, passed in sorted(cand[i].checks.items())
            if not passed
        ],
        categories=[
            CategoryDelta(
                category=category,
                metric=metric,
                cases=len(values),
                baseline_mean=fmean(b for b, _ in values),
                candidate_mean=fmean(c for _, c in values),
                delta_abs=fmean(c for _, c in values) - fmean(b for b, _ in values),
            )
            for (category, metric), values in sorted(by_category.items())
        ],
    )
