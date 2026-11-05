"""Per-category regression limits (metrics.<name>.max_category_regression_*)."""

from typing import Any

from conftest import POLICY, case, make_policy, make_run

from evalgate.engine import evaluate
from evalgate.models import Report
from evalgate.reporting.html import render_html


def cat(case_id: str, category: str | None, quality: float, **over: Any) -> dict[str, Any]:
    meta = {"metadata": {"category": category}} if category is not None else {}
    return case(case_id, quality, **meta, **over)


# Overall quality improves (+0.033) while the "hard" category drops 0.1.
BASE = [cat(f"e{i}", "easy", 0.8) for i in range(4)] + [cat(f"h{i}", "hard", 0.8) for i in range(2)]
CAND = [cat(f"e{i}", "easy", 0.9) for i in range(4)] + [cat(f"h{i}", "hard", 0.7) for i in range(2)]


def policy(**quality: Any) -> Any:
    metrics = POLICY["metrics"] | {"quality": POLICY["metrics"]["quality"] | quality}
    return make_policy(metrics=metrics)


def rules(report: Report) -> dict[str, bool]:
    return {r.rule: r.passed for r in report.rules}


def test_category_regression_hidden_by_overall_gain_fails() -> None:
    report = evaluate(make_run(BASE), make_run(CAND), policy(max_category_regression_abs=0.05))
    assert report.status == "FAIL"
    r = rules(report)
    assert r["metrics.quality.max_mean_regression_abs"] is True
    assert r["metrics.quality.categories.hard.max_category_regression_abs"] is False
    assert r["metrics.quality.categories.easy.max_category_regression_abs"] is True
    # Without the category limit the same runs pass: the overall mean improved.
    assert evaluate(make_run(BASE), make_run(CAND), policy()).status == "PASS"


def test_category_pct_limit() -> None:
    report = evaluate(make_run(BASE), make_run(CAND), policy(max_category_regression_pct=10))
    assert rules(report)["metrics.quality.categories.hard.max_category_regression_pct"] is False
    hard = next(r for r in report.rules if "hard" in r.rule)
    assert "quality [hard] mean 0.8 -> 0.7" in hard.message


def test_policy_without_category_limits_adds_no_rules() -> None:
    report = evaluate(make_run(BASE), make_run(CAND), policy())
    assert not any(".categories." in r for r in rules(report))
    assert report.comparison is not None and report.comparison.categories  # still reported


def test_category_limit_without_category_metadata_is_invalid() -> None:
    base = [cat(f"c{i}", None, 0.8) for i in range(3)]
    report = evaluate(make_run(base), make_run(base), policy(max_category_regression_abs=0.05))
    assert report.status == "INVALID"
    assert [d.code for d in report.diagnostics if d.level == "error"] == ["category_missing"]


def test_uncategorized_cases_warn() -> None:
    base = [*BASE, cat("x", None, 0.8)]
    cand = [*CAND, cat("x", None, 0.8)]
    report = evaluate(make_run(base), make_run(cand), policy(max_category_regression_abs=0.05))
    assert report.status == "FAIL"
    partial = [d for d in report.diagnostics if d.code == "category_partial"]
    assert partial[0].level == "warning" and "1 of 7" in partial[0].message


def test_category_pct_at_zero_baseline_fails_closed() -> None:
    base = [cat("a", "z", 0.0), cat("b", "z", 0.0), cat("c", "y", 0.5)]
    cand = [cat("a", "z", 0.0), cat("b", "z", 0.0), cat("c", "y", 0.5)]
    pct_only = evaluate(make_run(base), make_run(cand), policy(max_category_regression_pct=5))
    assert pct_only.status == "INVALID"
    undefined = next(d for d in pct_only.diagnostics if d.code == "pct_undefined")
    assert (
        "category 'z'" in undefined.message and "max_category_regression_abs" in undefined.message
    )
    both = policy(max_category_regression_pct=5, max_category_regression_abs=0.01)
    assert evaluate(make_run(base), make_run(cand), both).status == "PASS"


def test_single_case_category_is_inconclusive_under_require_conclusive() -> None:
    base, cand = [*BASE, cat("s", "solo", 0.8)], [*CAND[:4], *BASE[4:], cat("s", "solo", 0.8)]
    p = policy(max_category_regression_abs=0.05).model_copy(
        update={"statistics": make_policy(statistics={"require_conclusive": True}).statistics}
    )
    report = evaluate(make_run(base), make_run(cand), p)
    solo = next(r for r in report.rules if ".solo." in r.rule)
    assert solo.passed is False and solo.conclusive is False


def test_category_ci_reported_and_rendered() -> None:
    report = evaluate(make_run(BASE), make_run(CAND), policy(max_category_regression_abs=0.05))
    assert report.comparison is not None
    hard = next(
        g for g in report.comparison.categories if (g.category, g.metric) == ("hard", "quality")
    )
    assert hard.ci.low <= hard.delta_abs <= hard.ci.high
    assert hard.delta_pct is not None and round(hard.delta_pct, 6) == -12.5
    assert "[-0.1, -0.1]" in render_html(report)
