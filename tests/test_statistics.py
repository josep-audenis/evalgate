"""Confidence intervals: correctness against references, determinism, and gate semantics."""

import random
from math import sqrt
from statistics import NormalDist, fmean, stdev
from typing import Any

import pytest
from conftest import POLICY, case, make_run
from pydantic import ValidationError

from evalgate.comparison.stats import BOOTSTRAP_MAX_N, RESAMPLES, mean_diff_ci
from evalgate.engine import evaluate
from evalgate.models import Policy, Report


def normal_ci(d: list[float], conf: float = 0.95) -> tuple[float, float]:
    half = NormalDist().inv_cdf(1 - (1 - conf) / 2) * stdev(d) / sqrt(len(d))
    return fmean(d) - half, fmean(d) + half


def test_normal_method_matches_textbook_formula() -> None:
    rng = random.Random(3)
    d = [rng.gauss(0.1, 1.0) for _ in range(BOOTSTRAP_MAX_N + 1)]
    low, high, method, resamples = mean_diff_ci(d, 0.95)
    assert method == "normal" and resamples == 0
    assert (low, high) == pytest.approx(normal_ci(d))


def test_bootstrap_agrees_with_normal_on_symmetric_data() -> None:
    rng = random.Random(7)
    d = [rng.gauss(0.05, 0.2) for _ in range(800)]
    low, high, method, resamples = mean_diff_ci(d, 0.95)
    assert method == "bootstrap" and resamples == RESAMPLES
    ref_low, ref_high = normal_ci(d)
    width = ref_high - ref_low
    assert abs(low - ref_low) < 0.05 * width and abs(high - ref_high) < 0.05 * width


def test_bootstrap_coverage_close_to_nominal() -> None:
    hits = 0
    for s in range(200):
        rng = random.Random(100 + s)
        d = [rng.gauss(0.03, 0.3) for _ in range(50)]
        low, high, *_ = mean_diff_ci(d, 0.95)
        hits += low <= 0.03 <= high
    assert 0.90 <= hits / 200 <= 0.99


def test_ci_is_deterministic_and_contains_mean() -> None:
    d = [random.Random(1).random() - 0.5 for _ in range(300)]
    first = mean_diff_ci(d, 0.9)
    assert first == mean_diff_ci(list(d), 0.9)
    assert first[0] <= fmean(d) <= first[1]


def test_degenerate_cases() -> None:
    assert mean_diff_ci([0.25], 0.95) == (0.25, 0.25, "bootstrap", 0)
    low, high, *_ = mean_diff_ci([0.1] * 50, 0.95)
    assert low == pytest.approx(0.1) and high == pytest.approx(0.1)


# --- gate semantics --------------------------------------------------------------------

QUALITY_ONLY = {
    "metrics": {
        "quality": {
            "path": "scores.quality",
            "direction": "maximize",
            "max_mean_regression_abs": 0.05,
        }
    },
    "checks": {},
}


def policy(**statistics: Any) -> Policy:
    return Policy.model_validate(POLICY | QUALITY_ONLY | {"statistics": statistics})


def noisy_runs(shift: float, noise: float, n: int = 40) -> tuple[Any, Any]:
    """Candidate = baseline + shift ± noise (alternating), so the mean change is exactly `shift`
    and the CI half-width is about 1.96 * noise / sqrt(n)."""
    base = [0.5 + 0.01 * (i % 7) for i in range(n)]
    cand = [b + shift + (noise if i % 2 else -noise) for i, b in enumerate(base)]
    return (
        make_run([case(f"c{i}", q) for i, q in enumerate(base)]),
        make_run([case(f"c{i}", q) for i, q in enumerate(cand)]),
    )


def quality_rule(report: Report) -> Any:
    return next(r for r in report.rules if r.rule.startswith("metrics.quality"))


def test_clear_pass_is_conclusive() -> None:
    report = evaluate(*noisy_runs(shift=0.0, noise=0.01), policy(require_conclusive=True))
    assert report.status == "PASS" and quality_rule(report).conclusive is True


def test_inconclusive_pass_reported_but_not_gated_by_default() -> None:
    runs = noisy_runs(shift=-0.03, noise=0.2)  # point within 0.05, CI spans it
    report = evaluate(*runs, policy())
    rule = quality_rule(report)
    assert report.status == "PASS" and rule.passed and rule.conclusive is False
    assert "inconclusive" in rule.message


def test_require_conclusive_turns_inconclusive_pass_into_fail() -> None:
    report = evaluate(*noisy_runs(shift=-0.03, noise=0.2), policy(require_conclusive=True))
    rule = quality_rule(report)
    assert report.status == "FAIL" and not rule.passed and rule.conclusive is False
    assert "require_conclusive" in rule.message


def test_gate_never_turns_fail_into_pass() -> None:
    runs = noisy_runs(shift=-0.08, noise=0.3)  # point beyond 0.05, CI spans it
    for gate in (False, True):
        rule = quality_rule(evaluate(*runs, policy(require_conclusive=gate)))
        assert not rule.passed and rule.conclusive is False


def test_confidence_level_widens_interval() -> None:
    runs = noisy_runs(shift=0.0, noise=0.1)
    ci80 = evaluate(*runs, policy(confidence=0.8)).comparison.metrics[0].ci  # type: ignore[union-attr]
    ci99 = evaluate(*runs, policy(confidence=0.99)).comparison.metrics[0].ci  # type: ignore[union-attr]
    assert ci99.low < ci80.low and ci99.high > ci80.high
    assert ci80.confidence == 0.8


def test_pct_bounds_scale_abs_bounds() -> None:
    ci = evaluate(*noisy_runs(0.02, 0.05), policy()).comparison.metrics[0].ci  # type: ignore[union-attr]
    base_mean = fmean(c.scores["quality"] for c in noisy_runs(0.02, 0.05)[0].cases)
    assert ci.pct_low == pytest.approx(ci.low / base_mean * 100)
    assert ci.pct_high == pytest.approx(ci.high / base_mean * 100)


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 1},
        {"confidence": 0},
        {"confidence": "0.9"},
        {"require_conclusive": "yes"},
        {"seed": 1},
    ],
)
def test_statistics_policy_validation(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        policy(**bad)
