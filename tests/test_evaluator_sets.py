"""Pair checks on native evaluator sets (native-evaluators design §4)."""

from typing import Any

import pytest
from conftest import case, make_policy, make_run

from evalgate.engine import evaluate
from evalgate.models import Report, Run

V12 = {"schema_version": "1.2"}
SET = {"is_json": "1+aaa", "short": "1+bbb"}


def run(evaluators: dict[str, str] | None) -> Run:
    manifest: dict[str, Any] = V12 | {"evaluators": evaluators} if evaluators else {}
    return make_run([case("a"), case("b")], **manifest)


def diags(report: Report) -> list[tuple[str, str]]:
    return [(d.level, d.code) for d in report.diagnostics]


def test_identical_sets_pass() -> None:
    report = evaluate(run(SET), run(dict(SET)), make_policy())
    assert report.status == "PASS" and diags(report) == []


def test_runs_without_evaluators_get_neither_code() -> None:
    report = evaluate(run(None), run(None), make_policy())
    assert not [c for _, c in diags(report) if c.startswith("evaluator_set")]


@pytest.mark.parametrize(
    "cand",
    [
        {"is_json": "1+aaa", "short": "1+ccc"},  # changed version (e.g. an argument)
        SET | {"extra": "1"},  # added evaluator
        {"is_json": "1+aaa"},  # removed evaluator
        {"is_json": "1+aaa", "renamed": "1+bbb"},
    ],
)
def test_differing_sets_are_invalid_by_default(cand: dict[str, str]) -> None:
    report = evaluate(run(SET), run(cand), make_policy())
    assert report.status == "INVALID"
    assert ("error", "evaluator_set_mismatch") in diags(report)


def test_mismatch_message_names_the_differences() -> None:
    report = evaluate(run(SET), run({"is_json": "1+aaa", "short": "1+ccc"}), make_policy())
    message = next(d.message for d in report.diagnostics if d.code == "evaluator_set_mismatch")
    assert "short (1+bbb vs 1+ccc)" in message and "is_json" not in message


@pytest.mark.parametrize("base,cand", [(SET, None), (None, SET)])
def test_only_one_run_declaring_evaluators_is_invalid(
    base: dict[str, str] | None, cand: dict[str, str] | None
) -> None:
    report = evaluate(run(base), run(cand), make_policy())
    assert report.status == "INVALID"
    assert ("error", "evaluator_set_partial") in diags(report)


@pytest.mark.parametrize(
    "base,cand,code",
    [
        (SET, {"is_json": "2"}, "evaluator_set_mismatch"),
        (SET, None, "evaluator_set_partial"),
    ],
)
def test_policy_opt_out_downgrades_to_warning(
    base: dict[str, str], cand: dict[str, str] | None, code: str
) -> None:
    policy = make_policy(comparison={"require_same_evaluator": False})
    report = evaluate(run(base), run(cand), policy)
    assert ("warning", code) in diags(report)
    assert report.status == "PASS"
