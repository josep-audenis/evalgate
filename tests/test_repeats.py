"""Repeated runs (docs/design/repeated-runs.md): combined per case, fail closed."""

from pathlib import Path
from statistics import stdev
from typing import Any

import pytest
from conftest import MANIFEST, case, make_policy, make_run

import evalgate
from evalgate import EvalRun
from evalgate.comparison.repeats import collapse
from evalgate.engine import evaluate
from evalgate.io import load_run
from evalgate.models import Report, Run

V11 = {"schema_version": "1.1"}


def rep(case_id: str, r: int, quality: float = 0.9, **over: Any) -> dict[str, Any]:
    return case(case_id, quality, repeat=r, **over)


def repeated(qualities: dict[str, list[float]], **manifest: Any) -> Run:
    cases = [rep(i, r, q) for i, qs in qualities.items() for r, q in enumerate(qs)]
    return make_run(cases, **(V11 | manifest))


def codes(report: Report) -> set[str]:
    return {d.code for d in report.diagnostics if d.level == "error"}


# --- validation: fail closed --------------------------------------------------------------


def test_repeats_require_schema_1_1() -> None:
    run = make_run([rep("a", 0), rep("a", 1)])  # schema 1.0
    assert codes(evaluate(run, run, make_policy())) == {"repeat_schema"}


def test_mixed_repeat_presence_is_invalid() -> None:
    run = make_run([rep("a", 0), case("b")], **V11)
    assert codes(evaluate(run, run, make_policy())) == {"repeat_mixed"}


def test_duplicate_repeat_is_invalid() -> None:
    run = make_run([rep("a", 0), rep("a", 0)], **V11)
    report = evaluate(run, run, make_policy())
    assert codes(report) == {"duplicate_case_id"}
    assert "(case_id, repeat)" in report.diagnostics[0].message


def test_missing_repeat_is_invalid() -> None:
    run = make_run([rep("a", 0), rep("a", 1), rep("b", 0)], **V11)  # b lacks repeat 1
    assert codes(evaluate(run, run, make_policy())) == {"repeat_incomplete"}


def test_declared_repeats_must_be_met() -> None:
    run = repeated({"a": [0.9, 0.9]}, repeats=3)
    assert codes(evaluate(run, run, make_policy())) == {"repeat_incomplete"}


def test_repeat_numbers_must_start_at_zero() -> None:
    run = make_run([rep("a", 1), rep("a", 2)], **V11)
    assert codes(evaluate(run, run, make_policy())) == {"repeat_incomplete"}


# --- combining repeats ----------------------------------------------------------------------


def test_collapse_means_strict_checks_and_errors() -> None:
    run = make_run(
        [
            rep("a", 0, 0.6),
            rep("a", 1, 0.9),
            rep("b", 0, checks={"safety_valid": True, "schema_valid": True}),
            rep("b", 1, checks={"safety_valid": False, "schema_valid": True}),
            rep("c", 0),
            {"case_id": "c", "repeat": 1, "status": "error", "error": "timeout"},
        ],
        **V11,
    )
    by_id = {c.case_id: c for c in collapse(run).cases}
    assert by_id["a"].scores["quality"] == pytest.approx(0.75)
    assert by_id["b"].checks == {"safety_valid": False, "schema_valid": True}  # strict
    assert by_id["c"].status == "error" and by_id["c"].error == "repeat 1: timeout"


def test_metric_missing_in_one_repeat_is_invalid() -> None:
    run = make_run([rep("a", 0), rep("a", 1, scores={})], **V11)
    assert codes(evaluate(run, run, make_policy())) == {"repeat_inconsistent"}


def test_check_in_some_repeats_only_cannot_hide_a_failure() -> None:
    # Review finding: a check absent from one repeat on both sides used to vanish -> PASS,
    # while the same data as a plain run FAILs.
    base = make_run(
        [
            rep("a", 0, checks={"safety_valid": True, "x": True}),
            rep("a", 1, checks={"safety_valid": True}),
        ],
        **V11,
    )
    cand = make_run(
        [
            rep("a", 0, checks={"safety_valid": True, "x": False}),
            rep("a", 1, checks={"safety_valid": True}),
        ],
        **V11,
    )
    report = evaluate(base, cand, make_policy())
    assert report.status == "INVALID" and codes(report) == {"repeat_inconsistent"}


def test_errored_repeat_without_fields_is_not_inconsistent() -> None:
    run = make_run([rep("a", 0), {"case_id": "a", "repeat": 1, "status": "error"}], **V11)
    assert "repeat_inconsistent" not in codes(evaluate(run, run, make_policy()))


def test_differing_metadata_warns() -> None:
    run = make_run(
        [rep("a", 0, metadata={"category": "x"}), rep("a", 1, metadata={"category": "y"})], **V11
    )
    report = evaluate(run, run, make_policy())
    assert ("warning", "repeat_metadata") in {(d.level, d.code) for d in report.diagnostics}


def test_single_repeat_matches_plain_run() -> None:
    plain_b = make_run([case("a", 0.8), case("b", 0.9)])
    plain_c = make_run([case("a", 0.7), case("b", 0.95)])
    rep_b = repeated({"a": [0.8], "b": [0.9]})
    rep_c = repeated({"a": [0.7], "b": [0.95]})
    a, b = evaluate(plain_b, plain_c, make_policy()), evaluate(rep_b, rep_c, make_policy())
    assert a.status == b.status
    assert a.comparison is not None and b.comparison is not None
    assert a.comparison.metrics == b.comparison.metrics
    assert b.comparison.repeats is None  # k = 1 on both sides: no repeat section


# --- cases stay the unit of analysis --------------------------------------------------------


def test_repeats_do_not_inflate_sample_size() -> None:
    base = repeated({f"c{i}": [0.9, 0.8, 0.85] for i in range(5)})
    cand = repeated({f"c{i}": [0.9, 0.7, 0.8] for i in range(5)})
    report = evaluate(base, cand, make_policy(comparison={"min_compared_cases": 6}))
    assert codes(report) == {"too_few_cases"}  # 5 cases, not 15 repeats
    report = evaluate(base, cand, make_policy())
    assert report.comparison is not None
    assert report.comparison.compared_cases == 5
    assert report.comparison.metrics[1].cases == 5  # quality CI over 5 cases


def test_different_repeat_counts_and_noise_summary() -> None:
    base = repeated({"a": [0.8, 0.9], "b": [0.6, 0.6]})
    cand = repeated({"a": [0.7, 0.9, 0.8], "b": [0.5, 0.7, 0.6]})
    report = evaluate(base, cand, make_policy())
    rep_summary = report.comparison.repeats  # type: ignore[union-attr]
    assert rep_summary is not None
    assert (rep_summary.baseline_repeats, rep_summary.candidate_repeats) == (2, 3)
    quality = next(m for m in rep_summary.metrics if m.name == "quality")
    assert quality.baseline_sd == pytest.approx((stdev([0.8, 0.9]) + 0.0) / 2)
    assert quality.candidate_sd == pytest.approx(
        (stdev([0.7, 0.9, 0.8]) + stdev([0.5, 0.7, 0.6])) / 2
    )


def test_flaky_check_is_newly_failing_and_pass_rate_reported() -> None:
    ok = {"safety_valid": True, "schema_valid": True}
    flaky = {"safety_valid": False, "schema_valid": True}
    base = make_run([rep("a", 0, checks=ok), rep("a", 1, checks=ok)], **V11)
    cand = make_run([rep("a", 0, checks=ok), rep("a", 1, checks=flaky)], **V11)
    report = evaluate(base, cand, make_policy())
    assert report.status == "FAIL"
    assert {r.rule for r in report.rules if not r.passed} == {
        "checks.required.safety_valid",
        "checks.max_new_failures",
    }
    rates = {c.check: c for c in report.comparison.repeats.checks}  # type: ignore[union-attr]
    assert rates["safety_valid"].baseline_pass_rate == 1.0
    assert rates["safety_valid"].candidate_pass_rate == 0.5


def test_errored_repeat_counts_against_execution_errors() -> None:
    base = repeated({"a": [0.9, 0.9], "b": [0.9, 0.9]})
    cand = make_run(
        [rep("a", 0), rep("a", 1), rep("b", 0), {"case_id": "b", "repeat": 1, "status": "error"}],
        **V11,
    )
    report = evaluate(base, cand, make_policy())
    assert report.comparison is not None and report.comparison.candidate_errors == ["b"]
    assert {r.rule for r in report.rules if not r.passed} == {"max_execution_errors"}


def test_overflow_while_combining_is_invalid() -> None:
    run = repeated({"a": [1.7e308, 1.7e308]})
    report = evaluate(run, run, make_policy())
    assert report.status == "INVALID" and codes(report) == {"numeric_overflow"}


# --- SDK -------------------------------------------------------------------------------------

IDS = {"dataset": "suite", "dataset_version": "v1", "evaluator_version": "e1"}


def test_sdk_records_repeats_and_round_trips(tmp_path: Path) -> None:
    with EvalRun("r", path=tmp_path / "r", repeats=2, **IDS) as run:
        for i in range(2):
            run.record("a", repeat=i, scores={"quality": 0.5 + i / 10})
        run.record_error("b", "timeout", repeat=0)
        run.record("b", repeat=1, scores={"quality": 0.9})
    loaded = load_run(tmp_path / "r")
    assert loaded.manifest.schema_version == "1.1" and loaded.manifest.repeats == 2
    assert [(c.case_id, c.repeat, c.status) for c in loaded.cases] == [
        ("a", 0, "ok"),
        ("a", 1, "ok"),
        ("b", 0, "error"),
        ("b", 1, "ok"),
    ]


def test_sdk_rejects_bad_repeat_usage(tmp_path: Path) -> None:
    with EvalRun("r", path=tmp_path / "r", repeats=2, **IDS) as run:
        with pytest.raises(ValueError, match="repeat must be 0..1"):
            run.record("a", scores={"quality": 0.5})  # repeat missing
        with pytest.raises(ValueError, match="repeat must be 0..1"):
            run.record("a", repeat=2)
        run.record("a", repeat=0)
        with pytest.raises(ValueError, match="duplicate repeat 0"):
            run.record("a", repeat=0)
    with EvalRun("p", path=tmp_path / "p", **IDS) as run:
        with pytest.raises(ValueError, match="repeats=k"):
            run.record("a", repeat=0)


def test_sdk_incomplete_repeats_are_invalid_at_compare(tmp_path: Path) -> None:
    import yaml
    from conftest import POLICY

    for name in ("base", "cand"):
        with EvalRun(name, path=tmp_path / name, repeats=3, **IDS) as run:
            for i in range(3 if name == "base" else 2):  # candidate recorded only 2 of 3
                run.record(
                    "a",
                    repeat=i,
                    scores={"quality": 0.9},
                    metrics={"latency_ms": 1.0},
                    checks={"safety_valid": True},
                )
    policy = tmp_path / "p.yaml"
    policy.write_text(yaml.safe_dump(POLICY))
    report = evalgate.compare(tmp_path / "base", tmp_path / "cand", policy)
    assert report.status == "INVALID" and codes(report) == {"repeat_incomplete"}


def test_plain_sdk_run_still_writes_schema_1_0(tmp_path: Path) -> None:
    with EvalRun("r", path=tmp_path / "r", **IDS) as run:
        run.record("a", scores={"quality": 0.9})
    assert load_run(tmp_path / "r").manifest.schema_version == MANIFEST["schema_version"]
    assert "repeats" not in (tmp_path / "r" / "run.json").read_text()


def test_plain_run_report_manifest_has_no_repeats_key() -> None:
    # 0.2.0 compatibility: plain runs serialize exactly as before schema 1.1.
    plain = make_run([case("a")])
    dumped = evaluate(plain, plain, make_policy()).model_dump()
    assert "repeats" not in dumped["baseline"] and "repeats" not in dumped["candidate"]
    rep = repeated({"a": [0.9, 0.9]}, repeats=2)
    assert evaluate(rep, rep, make_policy()).model_dump()["baseline"]["repeats"] == 2
