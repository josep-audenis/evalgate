"""The one decision pipeline shared by CLI and SDK: validate -> compare -> apply policy."""

import math

from evalgate._version import __version__
from evalgate.comparison import compare
from evalgate.models import Comparison, Diagnostic, Policy, Report, Run
from evalgate.policies import evaluate_policy
from evalgate.validation import validate_pair, validate_run


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


def _all_finite(c: Comparison) -> bool:
    values = [x for m in c.metrics for x in (m.baseline_mean, m.candidate_mean, m.delta_abs)]
    values += [m.delta_pct for m in c.metrics if m.delta_pct is not None]
    values += [d.delta_abs for d in (*c.regressed_cases, *c.improved_cases)]
    values += [x for g in c.categories for x in (g.baseline_mean, g.candidate_mean, g.delta_abs)]
    return all(math.isfinite(x) for x in values)


def invalid_report(diagnostics: list[Diagnostic], **identity: object) -> Report:
    return Report.model_validate(
        {"evalgate_version": __version__, "status": "INVALID", "diagnostics": diagnostics}
        | identity
    )


def evaluate(baseline: Run, candidate: Run, policy: Policy) -> Report:
    """Decide PASS / FAIL / INVALID. Never PASS on input that failed validation."""
    identity = {"baseline": baseline.manifest, "candidate": candidate.manifest, "policy": policy}
    diagnostics = validate_run(baseline, "baseline") + validate_run(candidate, "candidate")
    if not _has_errors(diagnostics):
        diagnostics += validate_pair(baseline, candidate, policy)
    if _has_errors(diagnostics):
        return invalid_report(diagnostics, **identity)

    try:
        comparison = compare(baseline, candidate, policy.metrics)
    except OverflowError:
        comparison = None
    if comparison is None or not _all_finite(comparison):
        overflow = Diagnostic(
            level="error",
            code="numeric_overflow",
            message="metric values too large to average or subtract (result is not finite)",
        )
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
