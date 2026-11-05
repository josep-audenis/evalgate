"""Per-category regression limits (metrics.<name>.max_category_regression_*)."""

from typing import Any

import pytest
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


# Review findings (PR #15).


def test_unlimited_tiny_baseline_category_does_not_turn_invalid() -> None:
    # Percent change of a category with a denormal baseline mean is inf; no rule reads it.
    base = [cat("t", "tiny", 5e-324), cat("b1", "big", 0.8), cat("b2", "big", 0.8)]
    cand = [cat("t", "tiny", 0.01), cat("b1", "big", 0.8), cat("b2", "big", 0.8)]
    assert evaluate(make_run(base), make_run(cand), policy()).status == "PASS"
    limited = policy(max_category_regression_pct=10)
    assert evaluate(make_run(base), make_run(cand), limited).status == "INVALID"


@pytest.mark.parametrize("bad", [1, None, "", "  ", ["x"]])
def test_non_text_category_is_invalid_with_category_limits(bad: Any) -> None:
    base = [case("a", 0.8, metadata={"category": bad}), cat("b", "s", 0.8)]
    cand = [case("a", 0.1, metadata={"category": bad}), cat("b", "s", 0.8)]
    report = evaluate(make_run(base), make_run(cand), policy(max_category_regression_abs=0.05))
    assert report.status == "INVALID"
    assert [d.code for d in report.diagnostics if d.level == "error"] == ["category_type"]
    # Without category limits the category is irrelevant and nothing changes.
    plain = evaluate(make_run(base), make_run(cand), policy())
    assert "category_type" not in {d.code for d in plain.diagnostics}


def test_minimize_metric_category_limit() -> None:
    base = [cat(f"e{i}", "easy", 0.8, latency=1000) for i in range(3)] + [
        cat("h", "hard", 0.8, latency=1000)
    ]
    cand = [cat(f"e{i}", "easy", 0.8, latency=800) for i in range(3)] + [
        cat("h", "hard", 0.8, latency=1300)
    ]
    metrics = POLICY["metrics"] | {
        "latency": POLICY["metrics"]["latency"] | {"max_category_regression_pct": 20}
    }
    r = rules(evaluate(make_run(base), make_run(cand), make_policy(metrics=metrics)))
    assert r["metrics.latency.max_mean_regression_pct"] is True  # overall latency improved
    assert r["metrics.latency.categories.hard.max_category_regression_pct"] is False
    assert r["metrics.latency.categories.easy.max_category_regression_pct"] is True


def test_varying_category_interval_and_conclusiveness() -> None:
    drops = [0.0, 0.02, 0.04, 0.06, 0.08, 0.1]
    base = [cat(f"c{i}", "v", 0.8) for i in range(6)]
    cand = [cat(f"c{i}", "v", 0.8 - d) for i, d in enumerate(drops)]
    p = policy(max_category_regression_abs=0.06)
    report = evaluate(make_run(base), make_run(cand), p)
    assert report.comparison is not None
    g = next(x for x in report.comparison.categories if x.metric == "quality")
    assert g.ci.low < g.delta_abs < g.ci.high  # a real, non-degenerate interval
    rule = next(x for x in report.rules if ".categories.v." in x.rule)
    assert rule.passed and not rule.conclusive  # mean drop 0.05 within 0.06, CI spans it
    strict = p.model_copy(
        update={"statistics": make_policy(statistics={"require_conclusive": True}).statistics}
    )
    assert evaluate(make_run(base), make_run(cand), strict).status == "FAIL"


def test_repeated_runs_take_category_from_first_repeat() -> None:
    def reps(q: float) -> list[dict[str, Any]]:
        return [
            cat(i, "hard" if i == "h" else "easy", q if i == "h" else 0.8, repeat=r)
            for i in ("e", "h")
            for r in range(2)
        ]

    manifest = {"schema_version": "1.1", "repeats": 2}
    report = evaluate(
        make_run(reps(0.8), **manifest),
        make_run(reps(0.7), **manifest),
        policy(max_category_regression_abs=0.05),
    )
    assert rules(report)["metrics.quality.categories.hard.max_category_regression_abs"] is False
