"""Paired check statistics: exact McNemar p-values and the opt-in significance rule."""

import random
from math import comb
from typing import Any

import pytest
from conftest import POLICY, case, make_run
from pydantic import ValidationError

from evalgate.comparison.stats import mcnemar_p
from evalgate.engine import evaluate
from evalgate.models import Policy, Report


def exact(b: int, c: int) -> float:
    n = b + c
    return sum(comb(n, k) for k in range(b, n + 1)) / 2**n


def test_hand_computed_reference() -> None:
    assert mcnemar_p(10, 2) == pytest.approx(79 / 4096)  # P(X>=10 | n=12)
    assert mcnemar_p(3, 0) == pytest.approx(0.125)
    assert mcnemar_p(4, 4) == pytest.approx(163 / 256)


def test_matches_exact_integer_arithmetic() -> None:
    rng = random.Random(5)
    for _ in range(500):
        b, c = rng.randint(1, 200), rng.randint(0, 200)
        assert mcnemar_p(b, c) == pytest.approx(exact(b, c), rel=1e-9)


def test_edge_cases() -> None:
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(0, 7) == 1.0  # only improvements: no evidence of regression
    assert mcnemar_p(30_000, 20_000) < 1e-100  # ~1e-1700 underflows to 0.0, never raises
    assert mcnemar_p(25_000, 25_000) == pytest.approx(0.5, abs=0.01)


# --- engine integration ------------------------------------------------------------------


def runs(flips_fail: int, flips_pass: int, stable: int = 20) -> tuple[Any, Any]:
    """`flips_fail` cases pass->fail, `flips_pass` fail->pass, `stable` pass in both."""
    ok, bad = {"safety_valid": True}, {"safety_valid": False}
    base, cand = [], []
    for i in range(flips_fail):
        base.append(case(f"f{i}", checks=ok))
        cand.append(case(f"f{i}", checks=bad))
    for i in range(flips_pass):
        base.append(case(f"p{i}", checks=bad))
        cand.append(case(f"p{i}", checks=ok))
    for i in range(stable):
        base.append(case(f"s{i}", checks=ok))
        cand.append(case(f"s{i}", checks=ok))
    return make_run(base), make_run(cand)


def policy(**checks: Any) -> Policy:
    # Keeps POLICY's metric rules: the significance flag alone is not a valid policy.
    return Policy.model_validate(
        POLICY | {"checks": {"fail_on_significant_regression": False} | checks}
    )


def sig_rules(report: Report) -> list[Any]:
    return [r for r in report.rules if r.rule.startswith("checks.significant_regression")]


def test_check_stats_counts() -> None:
    report = evaluate(*runs(5, 2, stable=10), policy(max_new_failures=100))
    assert report.comparison is not None
    [s] = report.comparison.check_stats
    assert (s.check, s.cases, s.newly_failing, s.newly_passing) == ("safety_valid", 17, 5, 2)
    assert (s.baseline_failures, s.candidate_failures) == (2, 5)
    assert s.p_value == pytest.approx(exact(5, 2))
    assert sig_rules(report) == []  # reported, but not a rule unless opted in


def test_significant_regression_fails_when_opted_in() -> None:
    report = evaluate(*runs(12, 1), policy(fail_on_significant_regression=True))
    [rule] = sig_rules(report)
    assert not rule.passed and report.status == "FAIL"
    assert "p=" in rule.message


def test_noisy_flips_pass_where_max_new_failures_would_fail() -> None:
    noisy = runs(6, 5)  # roughly balanced flips: noise, not regression
    sig = evaluate(*noisy, policy(fail_on_significant_regression=True))
    assert sig.status == "PASS" and sig_rules(sig)[0].passed
    strict = evaluate(*noisy, policy(max_new_failures=0))
    assert strict.status == "FAIL"


def test_alpha_follows_statistics_confidence() -> None:
    data = runs(9, 2)  # p ~= 0.033
    p = Policy.model_validate(
        POLICY
        | {"checks": {"fail_on_significant_regression": True}}
        | {"statistics": {"confidence": 0.99}}
    )
    assert evaluate(*data, p).status == "PASS"  # 0.033 >= alpha 0.01
    assert evaluate(*data, policy(fail_on_significant_regression=True)).status == "FAIL"


def test_flag_alone_is_rejected_because_it_cannot_fail_small_runs() -> None:
    with pytest.raises(ValidationError, match="no metrics or checks rules"):
        Policy.model_validate({"version": 1, "checks": {"fail_on_significant_regression": True}})
    # Why: 4 of 4 cases newly failing still gives p = 0.0625 > 0.05.
    assert mcnemar_p(4, 0) == pytest.approx(0.0625)


def test_flag_with_another_rule_is_valid_and_strictly_typed() -> None:
    Policy.model_validate(
        {"version": 1, "checks": {"fail_on_significant_regression": True, "max_new_failures": 3}}
    )
    with pytest.raises(ValidationError):
        Policy.model_validate(
            {"version": 1, "checks": {"fail_on_significant_regression": "yes", "required": ["x"]}}
        )
