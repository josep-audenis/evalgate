"""The one decision pipeline shared by CLI and SDK: validate -> compare -> apply policy."""

import math

from evalgate._version import __version__
from evalgate.comparison import compare
from evalgate.comparison.repeats import collapse, repeat_summary
from evalgate.models import CategoryDelta, Comparison, Diagnostic, MetricDelta, Policy, Report, Run
from evalgate.policies import evaluate_policy
from evalgate.validation import validate_pair, validate_run


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


def _all_finite(c: Comparison, policy: Policy) -> bool:
    # Only categories that rules read: an unused tiny-baseline category must not turn INVALID.
    limited = {
        name
        for name, r in policy.metrics.items()
        if r.max_category_regression_abs is not None or r.max_category_regression_pct is not None
    }
    deltas: list[MetricDelta | CategoryDelta] = [
        *c.metrics,
        *(g for g in c.categories if g.metric in limited),
    ]
    values = [
        x
        for m in deltas
        for x in (
            m.baseline_mean,
            m.candidate_mean,
            m.delta_abs,
            m.delta_pct,
            m.ci.low,
            m.ci.high,
            m.ci.pct_low,
            m.ci.pct_high,
        )
        if x is not None
    ]
    values += [d.delta_abs for d in (*c.regressed_cases, *c.improved_cases)]
    if c.repeats:
        values += [
            x for m in c.repeats.metrics for x in (m.baseline_sd, m.candidate_sd) if x is not None
        ]
    return all(math.isfinite(x) for x in values)


def invalid_report(diagnostics: list[Diagnostic], **identity: object) -> Report:
    return Report.model_validate(
        {"evalgate_version": __version__, "status": "INVALID", "diagnostics": diagnostics}
        | identity
    )


def evaluate(baseline: Run, candidate: Run, policy: Policy) -> Report:
    """Decide PASS / FAIL / INVALID. Never PASS on input that failed validation."""
    identity = {"baseline": baseline.manifest, "candidate": candidate.manifest, "policy": policy}
    overflow = Diagnostic(
        level="error",
        code="numeric_overflow",
        message="metric values too large to average or subtract (result is not finite)",
    )
    diagnostics = validate_run(baseline, "baseline") + validate_run(candidate, "candidate")
    if _has_errors(diagnostics):
        return invalid_report(diagnostics, **identity)
    try:
        # Repeats become one case each, so everything below works on cases (SPEC 9.2).
        base, cand = collapse(baseline), collapse(candidate)
    except OverflowError:  # fsum over finite values can only overflow
        return invalid_report([*diagnostics, overflow], **identity)
    diagnostics += validate_pair(base, cand, policy)
    if _has_errors(diagnostics):
        return invalid_report(diagnostics, **identity)

    b, c = {x.case_id: x for x in base.cases}, {x.case_id: x for x in cand.cases}
    compared = sorted(i for i in b.keys() & c.keys() if b[i].status == c[i].status == "ok")
    try:
        comparison: Comparison | None = compare(
            base, cand, policy.metrics, policy.statistics.confidence
        ).model_copy(
            update={"repeats": repeat_summary(baseline, candidate, compared, policy.metrics)}
        )
    except (OverflowError, ValueError):  # ValueError: inf - inf in fsum, StatisticsError
        comparison = None
    if comparison is None or not _all_finite(comparison, policy):
        return invalid_report([*diagnostics, overflow], **identity)

    rules, rule_diagnostics = evaluate_policy(comparison, policy)
    diagnostics += rule_diagnostics
    if _has_errors(diagnostics):
        status = "INVALID"
    else:
        status = "PASS" if all(r.passed for r in rules) else "FAIL"
    return Report.model_validate(
        {
            "evalgate_version": __version__,
            "status": status,
            "diagnostics": diagnostics,
            "comparison": comparison,
            "rules": rules,
        }
        | identity
    )
