"""The one decision pipeline shared by CLI and SDK: validate -> compare -> apply policy."""

from evalgate import __version__
from evalgate.comparison import compare
from evalgate.models import Diagnostic, Policy, Report, Run
from evalgate.policies import evaluate_policy
from evalgate.validation import validate_pair, validate_run


def _has_errors(diagnostics: list[Diagnostic]) -> bool:
    return any(d.level == "error" for d in diagnostics)


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

    comparison = compare(baseline, candidate, policy.metrics)
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
