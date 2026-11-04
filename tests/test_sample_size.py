"""Small samples must not produce silent, confident decisions."""

from typing import Any

import pytest
from conftest import POLICY, case, make_run
from pydantic import ValidationError

from evalgate.engine import evaluate
from evalgate.models import Policy, Report
from evalgate.validation import SMALL_SAMPLE


def policy(**comparison: Any) -> Policy:
    return Policy.model_validate(POLICY | {"comparison": comparison})


def runs(n: int, quality: float = 0.9) -> tuple[Any, Any]:
    return make_run([case(f"c{i}") for i in range(n)]), make_run(
        [case(f"c{i}", quality) for i in range(n)]
    )


def codes(report: Report) -> list[tuple[str, str]]:
    return [(d.level, d.code) for d in report.diagnostics]


def test_small_sample_warns_but_keeps_decision() -> None:
    report = evaluate(*runs(SMALL_SAMPLE - 1), policy())
    assert report.status == "PASS"
    assert codes(report) == [("warning", "small_sample")]
    regressed = evaluate(*runs(SMALL_SAMPLE - 1, quality=0.5), policy())
    assert regressed.status == "FAIL"  # a warning never softens a FAIL


def test_enough_cases_no_warning() -> None:
    assert codes(evaluate(*runs(SMALL_SAMPLE), policy())) == []


def test_errored_cases_do_not_count_toward_sample() -> None:
    base = make_run([case(f"c{i}") for i in range(SMALL_SAMPLE)])
    cand = make_run(
        [case(f"c{i}") for i in range(SMALL_SAMPLE - 1)]
        + [{"case_id": f"c{SMALL_SAMPLE - 1}", "status": "error"}]
    )
    report = evaluate(base, cand, policy())
    assert ("warning", "small_sample") in codes(report)


def test_min_compared_cases_gate_is_invalid_below() -> None:
    report = evaluate(*runs(9), policy(min_compared_cases=10))
    assert report.status == "INVALID"
    assert codes(report) == [("error", "too_few_cases")]
    assert report.comparison is None  # no decision on an insufficient sample


def test_min_compared_cases_met_replaces_default_warning() -> None:
    report = evaluate(*runs(10), policy(min_compared_cases=10))
    assert report.status == "PASS"
    assert codes(report) == []


@pytest.mark.parametrize("bad", [0, -1, True, "10"])
def test_min_compared_cases_must_be_positive_int(bad: object) -> None:
    with pytest.raises(ValidationError):
        policy(min_compared_cases=bad)
