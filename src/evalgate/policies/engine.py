"""Evaluate release rules against a comparison. Each rule yields one explainable result."""

from evalgate.models import CheckChange, Comparison, Diagnostic, Policy, RuleResult

# Absorbs float noise so a regression exactly at the threshold is not a violation.
_EPS = 1e-9


def _cases(changes: list[CheckChange]) -> str:
    return ", ".join(f"{c.case_id}/{c.check}" for c in changes)


def evaluate_policy(
    comparison: Comparison, policy: Policy
) -> tuple[list[RuleResult], list[Diagnostic]]:
    rules: list[RuleResult] = []
    diagnostics: list[Diagnostic] = []

    errors = comparison.candidate_errors
    rules.append(
        RuleResult(
            rule="max_execution_errors",
            passed=len(errors) <= policy.max_execution_errors,
            message=f"{len(errors)} candidate case(s) with status error "
            f"(max {policy.max_execution_errors})" + (f": {', '.join(errors)}" if errors else ""),
        )
    )

    deltas = {d.name: d for d in comparison.metrics}
    for name, rule in sorted(policy.metrics.items()):
        d = deltas[name]
        # Positive = worse, whatever the direction.
        regression = -d.delta_abs if rule.direction == "maximize" else d.delta_abs
        if rule.max_mean_regression_abs is not None:
            limit = rule.max_mean_regression_abs
            rules.append(
                RuleResult(
                    rule=f"metrics.{name}.max_mean_regression_abs",
                    passed=regression <= limit + _EPS,
                    message=f"{name} mean {d.baseline_mean:.6g} -> {d.candidate_mean:.6g}, "
                    f"regression {max(0.0, regression):.6g} (max {limit:g})",
                )
            )
        if rule.max_mean_regression_pct is not None:
            limit = rule.max_mean_regression_pct
            if d.delta_pct is None:
                # Percent change undefined at a zero baseline: never divide, never guess.
                has_abs = rule.max_mean_regression_abs is not None
                diagnostics.append(
                    Diagnostic(
                        level="warning" if has_abs else "error",
                        code="pct_undefined",
                        message=f"metric '{name}': baseline mean is 0, percent change undefined"
                        + (
                            "; absolute threshold applied"
                            if has_abs
                            else "; set max_mean_regression_abs"
                        ),
                    )
                )
                continue
            regression_pct = -d.delta_pct if rule.direction == "maximize" else d.delta_pct
            rules.append(
                RuleResult(
                    rule=f"metrics.{name}.max_mean_regression_pct",
                    passed=regression_pct <= limit + _EPS,
                    message=f"{name} mean {d.baseline_mean:.6g} -> {d.candidate_mean:.6g}, "
                    f"regression {max(0.0, regression_pct):.4g}% (max {limit:g}%)",
                )
            )

    for check in sorted(set(policy.checks.required)):
        failing = [c for c in comparison.candidate_failing_checks if c.check == check]
        rules.append(
            RuleResult(
                rule=f"checks.required.{check}",
                passed=not failing,
                message=f"required check '{check}' "
                + (f"failed in {len(failing)} case(s): {_cases(failing)}" if failing else "passed"),
            )
        )

    if policy.checks.max_new_failures is not None:
        new = comparison.newly_failing_checks
        rules.append(
            RuleResult(
                rule="checks.max_new_failures",
                passed=len(new) <= policy.checks.max_new_failures,
                message=f"{len(new)} newly failing check(s) "
                f"(max {policy.checks.max_new_failures})" + (f": {_cases(new)}" if new else ""),
            )
        )
    return rules, diagnostics
