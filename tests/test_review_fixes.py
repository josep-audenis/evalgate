"""Regression tests for findings from the independent review (fail-closed gaps)."""

from pathlib import Path
from typing import Any

import pytest
from conftest import POLICY, case, make_policy, make_run
from pydantic import ValidationError
from typer.testing import CliRunner

from evalgate import EvalRun
from evalgate.cli import EXIT_FAIL, app
from evalgate.engine import evaluate
from evalgate.io import InputError, load_policy, load_run
from evalgate.models import Policy, Report

LOOSE = make_policy(comparison={"require_same_cases": False})


def failed(report: Report) -> set[str]:
    return {r.rule for r in report.rules if not r.passed}


def test_markup_in_ids_is_printed_not_parsed(tmp_path: Path) -> None:
    from conftest import write_run

    base = write_run(tmp_path / "b", [case("x[/]", 0.9), case("[red]ok  [/]", 0.9)])
    cand = write_run(
        tmp_path / "c", [case("x[/]", 0.1), case("[red]ok  [/]", 0.9)], run_id="[/bold]"
    )
    policy = tmp_path / "p.yaml"
    policy.write_text("version: 1\nchecks: {max_new_failures: 0}\n" + _quality_rule())
    out = tmp_path / "r.json"
    result = CliRunner().invoke(
        app, ["compare", str(base), str(cand), "--policy", str(policy), "--json", str(out)]
    )
    assert result.exit_code == EXIT_FAIL, result.output
    assert "x[/]" in result.output and "[/bold]" in result.output
    assert out.exists()


def _quality_rule() -> str:
    return (
        "metrics:\n  q: {path: scores.quality, direction: maximize, max_mean_regression_abs: 0}\n"
    )


def test_duplicate_policy_key_rejected(tmp_path: Path) -> None:
    p = tmp_path / "p.yaml"
    p.write_text(
        "version: 1\nmetrics:\n"
        "  q: {path: scores.q, direction: maximize, max_mean_regression_abs: 0}\n"
        "  q: {path: scores.q, direction: maximize, max_mean_regression_pct: 50}\n"
    )
    with pytest.raises(InputError) as exc:
        load_policy(p)
    assert exc.value.diagnostics[0].code == "policy_yaml"
    assert "duplicate key 'q'" in exc.value.diagnostics[0].message


@pytest.mark.parametrize(
    "data",
    [
        {"version": 1, "metrics": {"q": {"path": "scores.q", "direction": "maximize"}}},
        {"version": 1},
        {"version": True, "checks": {"max_new_failures": 0}},
        POLICY | {"max_execution_errors": True},
        POLICY | {"checks": {"required": [], "max_new_failures": "0"}},
        POLICY | {"comparison": {"require_same_cases": "no"}},
    ],
    ids=["no-threshold", "empty", "version-true", "errors-true", "string-int", "string-bool"],
)
def test_policy_that_could_wrongly_pass_is_rejected(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate(data)


def test_candidate_only_error_counts() -> None:
    cand = [case("a"), case("b"), {"case_id": "z", "status": "error"}]
    report = evaluate(make_run([case("a"), case("b")]), make_run(cand), LOOSE)
    assert report.status == "FAIL"
    assert failed(report) == {"max_execution_errors"}


def test_candidate_only_required_check_failure_counts() -> None:
    bad = case("z", checks={"safety_valid": False, "schema_valid": True})
    report = evaluate(make_run([case("a")]), make_run([case("a"), bad]), LOOSE)
    assert failed(report) == {"checks.required.safety_valid"}


def test_candidate_only_case_missing_required_check_is_invalid() -> None:
    report = evaluate(make_run([case("a")]), make_run([case("a"), case("z", checks={})]), LOOSE)
    assert report.status == "INVALID"


def test_overflow_is_invalid_not_a_crash() -> None:
    base = make_run([case("a", 1e308), case("b", 1e308)])
    report = evaluate(base, base, make_policy())
    assert report.status == "INVALID"
    assert [d.code for d in report.diagnostics] == ["numeric_overflow"]
    opposite = make_run([case("a", -1e308)])
    report = evaluate(make_run([case("a", 1e308)]), opposite, make_policy())
    assert report.status == "INVALID"  # subtraction to inf, no exception raised


def test_opted_out_mismatches_still_warn() -> None:
    policy = make_policy(
        comparison={"require_same_dataset": False, "require_same_evaluator": False}
    )
    cand = make_run([case("a")], dataset_version="v9", evaluator_version="e9")
    report = evaluate(make_run([case("a")]), cand, policy)
    assert report.status == "PASS"
    assert {(d.level, d.code) for d in report.diagnostics} == {
        ("warning", "dataset_mismatch"),
        ("warning", "evaluator_mismatch"),
    }


def test_no_negative_zero_in_messages() -> None:
    report = evaluate(make_run([case("a")]), make_run([case("a")]), make_policy())
    assert all("-0" not in r.message for r in report.rules)


IDS = {"dataset": "d", "dataset_version": "v1", "evaluator_version": "e1"}


def test_evalrun_rejects_manifest_key_collision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="dataset_id"):
        EvalRun("r", path=tmp_path / "r", dataset_id="other", **IDS)


def test_evalrun_accepts_numpy_like_bool_only(tmp_path: Path) -> None:
    class FakeNpBool:  # mimics a numpy bool scalar without needing numpy installed
        dtype = type("D", (), {"kind": "b"})()
        shape = ()

        def __bool__(self) -> bool:
            return True

    with EvalRun("r", path=tmp_path / "r", **IDS) as run:
        run.record("a", checks={"ok": FakeNpBool()})  # type: ignore[dict-item]
        with pytest.raises(ValidationError):
            run.record("b", checks={"ok": 1})  # type: ignore[dict-item]
    assert load_run(tmp_path / "r").cases[0].checks == {"ok": True}
