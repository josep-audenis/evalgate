"""Evaluate release rules against a comparison. Each rule yields one explainable result."""

from evalgate.models import (
    CategoryDelta,
    CheckChange,
    Comparison,
    Diagnostic,
    MetricDelta,
    Policy,
    RuleResult,
)

# Absorbs float noise so a regression exactly at the threshold is not a violation.
_EPS = 1e-9


def _cases(changes: list[CheckChange]) -> str:
    return ", ".join(f"{c.case_id}/{c.check}" for c in changes)


def _metric_rule(
    rule: str,
    means: str,
    delta: float,
    ci: tuple[float, float],
    limit: float,
    maximize: bool,
    unit: str,
    confidence: float,
    require_conclusive: bool,
    cases: int,
) -> RuleResult:
    """Threshold rule on the mean change. Regression is positive when worse, either direction."""
    # `+ 0.0` turns -0.0 from negating zero into 0.0, so messages never show "-0".
    regression = (-delta if maximize else delta) + 0.0
    reg_low, reg_high = (-ci[1] + 0.0, -ci[0] + 0.0) if maximize else ci
    within = regression <= limit + _EPS
    # Conclusive: the whole interval lies on one side of the limit.
    # One case has no variance: its zero-width interval proves nothing.
    conclusive = cases >= 2 and (reg_high <= limit + _EPS if within else reg_low > limit + _EPS)
    passed = within and (conclusive or not require_conclusive)

    fmt = ".4g" if unit else ".6g"
    message = (
        f"{means}, regression {max(0.0, regression):{fmt}}{unit} (max {limit:g}{unit}); "
        f"{confidence:.0%} CI of regression [{reg_low:{fmt}}{unit}, {reg_high:{fmt}}{unit}]"
    )
    if not conclusive:
        message += " - inconclusive"
        if within and require_conclusive:
            message += ": the interval exceeds the limit (statistics.require_conclusive)"
    return RuleResult(rule=rule, passed=passed, message=message, conclusive=conclusive)


def _threshold_rules(
    rule: str,
    what: str,
    label: str,
    d: MetricDelta | CategoryDelta,
    abs_limit: float | None,
    pct_limit: float | None,
    maximize: bool,
    policy: Policy,
    diagnostics: list[Diagnostic],
) -> list[RuleResult]:
    """Absolute and percent limits on one mean change (a whole metric or one category)."""
    means = f"{label} {d.baseline_mean:.6g} -> {d.candidate_mean:.6g}"
    stats = (d.ci.confidence, policy.statistics.require_conclusive, d.cases)
    rules = []
    if abs_limit is not None:
        ci = (d.ci.low, d.ci.high)
        rules.append(
            _metric_rule(f"{rule}_abs", means, d.delta_abs, ci, abs_limit, maximize, "", *stats)
        )
    if pct_limit is not None:
        if d.delta_pct is None:
            # Percent change undefined at a zero baseline: never divide, never guess.
            has_abs = abs_limit is not None
            diagnostics.append(
                Diagnostic(
                    level="warning" if has_abs else "error",
                    code="pct_undefined",
                    message=f"{what}: baseline mean is 0, percent change undefined"
                    + (
                        "; absolute threshold applied"
                        if has_abs
                        else f"; set {rule.rsplit('.', 1)[1]}_abs"
                    ),
                )
            )
        else:
            assert d.ci.pct_low is not None and d.ci.pct_high is not None  # baseline mean != 0
            ci = (d.ci.pct_low, d.ci.pct_high)
            rules.append(
                _metric_rule(
                    f"{rule}_pct", means, d.delta_pct, ci, pct_limit, maximize, "%", *stats
                )
            )
    return rules


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
        maximize = rule.direction == "maximize"
        d = deltas[name]
        rules += _threshold_rules(
            f"metrics.{name}.max_mean_regression",
            f"metric '{name}'",
            f"{name} mean",
            d,
            rule.max_mean_regression_abs,
            rule.max_mean_regression_pct,
            maximize,
            policy,
            diagnostics,
        )
        if rule.max_category_regression_abs is None and rule.max_category_regression_pct is None:
            continue
        groups = [g for g in comparison.categories if g.metric == name]
        uncovered = d.cases - sum(g.cases for g in groups)
        if not groups:
            # A category rule with nothing to apply to must not pass silently.
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="category_missing",
                    message=f"metric '{name}' has category limits but no compared case has "
                    "a text metadata.category",
                )
            )
        elif uncovered:
            diagnostics.append(
                Diagnostic(
                    level="warning",
                    code="category_partial",
                    message=f"metric '{name}': {uncovered} of {d.cases} compared case(s) have "
                    "no metadata.category; category limits don't cover them",
                )
            )
        for g in groups:
            rules += _threshold_rules(
                f"metrics.{name}.categories.{g.category}.max_category_regression",
                f"metric '{name}' category '{g.category}'",
                f"{name} [{g.category}] mean",
                g,
                rule.max_category_regression_abs,
                rule.max_category_regression_pct,
                maximize,
                policy,
                diagnostics,
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
    if policy.checks.fail_on_significant_regression:
        alpha = 1 - policy.statistics.confidence
        for s in comparison.check_stats:
            rules.append(
                RuleResult(
                    rule=f"checks.significant_regression.{s.check}",
                    passed=s.p_value >= alpha,
                    message=f"{s.check}: {s.newly_failing} newly failing, "
                    f"{s.newly_passing} newly passing over {s.cases} case(s); "
                    f"one-sided McNemar p={s.p_value:.3g} (alpha {alpha:.3g})",
                )
            )
    return rules, diagnostics
