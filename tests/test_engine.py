"""SPEC §12 Phase 1 required tests, run against the shared engine."""

from conftest import case, make_policy, make_run

from evalgate.engine import evaluate
from evalgate.models import Report


def codes(report: Report) -> set[str]:
    return {d.code for d in report.diagnostics if d.level == "error"}


def failed(report: Report) -> set[str]:
    return {r.rule for r in report.rules if not r.passed}


BASE = [case("a", 0.8, 1000), case("b", 0.9, 1200), case("c", 0.7, 800)]


def test_identical_runs_pass() -> None:
    report = evaluate(make_run(BASE), make_run(BASE), make_policy())
    assert report.status == "PASS"
    assert report.comparison is not None
    assert all(m.delta_abs == 0 for m in report.comparison.metrics)
    assert report.comparison.regressed_cases == report.comparison.improved_cases == []


def test_candidate_improvement_passes() -> None:
    cand = [case("a", 0.9, 900), case("b", 0.95, 1100), case("c", 0.8, 700)]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert report.status == "PASS"
    assert report.comparison is not None
    assert len(report.comparison.improved_cases) == 6
    quality = next(m for m in report.comparison.metrics if m.name == "quality")
    assert quality.delta_abs > 0 and quality.delta_pct is not None


def test_quality_regression_fails() -> None:
    cand = [case("a", 0.7), case("b", 0.9), case("c", 0.7)]  # mean -0.0333
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert report.status == "FAIL"
    assert failed(report) == {"metrics.quality.max_mean_regression_abs"}
    assert report.comparison is not None
    regressed = report.comparison.regressed_cases
    assert [d.case_id for d in regressed if d.metric == "quality"] == ["a"]


def test_regression_exactly_at_threshold_passes() -> None:
    cand = [case("a", 0.78), case("b", 0.88), case("c", 0.68)]  # mean -0.02, float noise
    assert evaluate(make_run(BASE), make_run(cand), make_policy()).status == "PASS"


def test_latency_direction_is_minimize() -> None:
    cand = [case("a", latency=1200), case("b", latency=1400), case("c", latency=1000)]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert failed(report) == {"metrics.latency.max_mean_regression_pct"}


def test_newly_failing_critical_check_fails_despite_better_quality() -> None:
    cand = [
        case("a", 0.95),
        case("b", 0.95, checks={"safety_valid": False, "schema_valid": True}),
        case("c", 0.95),
    ]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert report.status == "FAIL"
    assert failed(report) == {"checks.required.safety_valid", "checks.max_new_failures"}
    assert report.comparison is not None
    assert [(c.case_id, c.check) for c in report.comparison.newly_failing_checks] == [
        ("b", "safety_valid")
    ]


def test_missing_case_is_invalid() -> None:
    report = evaluate(make_run(BASE), make_run(BASE[:2]), make_policy())
    assert report.status == "INVALID"
    assert codes(report) == {"case_mismatch"}


def test_missing_case_allowed_only_when_policy_opts_out() -> None:
    policy = make_policy(comparison={"require_same_cases": False})
    report = evaluate(make_run(BASE), make_run(BASE[:2]), policy)
    assert report.status == "PASS"
    assert {d.code for d in report.diagnostics} == {"case_mismatch"}  # still warned


def test_duplicate_case_id_is_invalid() -> None:
    report = evaluate(make_run(BASE), make_run([*BASE, case("a")]), make_policy())
    assert report.status == "INVALID"
    assert codes(report) == {"duplicate_case_id"}


def test_missing_required_metric_is_invalid() -> None:
    cand = [case("a"), case("b"), case("c", scores={})]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert report.status == "INVALID"
    assert codes(report) == {"metric_missing"}


def test_missing_required_check_is_invalid() -> None:
    cand = [case("a"), case("b"), case("c", checks={"schema_valid": True})]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert codes(report) == {"check_missing"}


def test_dropped_optional_check_is_invalid() -> None:
    cand = [case("a"), case("b"), case("c", checks={"safety_valid": True})]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert codes(report) == {"check_missing"}


def test_incompatible_evaluator_is_invalid() -> None:
    report = evaluate(make_run(BASE), make_run(BASE, evaluator_version="e2"), make_policy())
    assert report.status == "INVALID"
    assert codes(report) == {"evaluator_mismatch"}


def test_incompatible_dataset_is_invalid() -> None:
    report = evaluate(make_run(BASE), make_run(BASE, dataset_version="v2"), make_policy())
    assert codes(report) == {"dataset_mismatch"}


def test_failed_pipeline_execution_fails_and_is_excluded_from_means() -> None:
    cand = [case("a"), case("b"), {"case_id": "c", "status": "error", "error": "timeout"}]
    report = evaluate(make_run(BASE), make_run(cand), make_policy())
    assert report.status == "FAIL"
    assert failed(report) == {"max_execution_errors"}
    assert report.comparison is not None
    assert report.comparison.candidate_errors == ["c"]
    assert report.comparison.compared_cases == 2


def test_execution_errors_allowed_by_policy() -> None:
    cand = [case("a", 0.8), case("b", 0.9), {"case_id": "c", "status": "error"}]
    report = evaluate(make_run(BASE), make_run(cand), make_policy(max_execution_errors=1))
    assert report.status == "PASS"


def test_all_cases_errored_is_invalid() -> None:
    cand = [{"case_id": i, "status": "error"} for i in "abc"]
    assert codes(evaluate(make_run(BASE), make_run(cand), make_policy())) == {"no_comparable_cases"}


def test_zero_baseline_pct_only_is_invalid() -> None:
    base = [case("a", latency=0), case("b", latency=0)]
    cand = [case("a", latency=5), case("b", latency=5)]
    report = evaluate(make_run(base), make_run(cand), make_policy())
    assert report.status == "INVALID"
    assert codes(report) == {"pct_undefined"}
    assert report.comparison is not None
    assert report.comparison.metrics[0].delta_pct is None  # latency sorts first


def test_zero_baseline_falls_back_to_abs_threshold() -> None:
    base = [case("a", latency=0), case("b", latency=0)]
    cand = [case("a", latency=5), case("b", latency=5)]
    metrics = {
        "latency": {
            "path": "metrics.latency_ms",
            "direction": "minimize",
            "max_mean_regression_abs": 1,
            "max_mean_regression_pct": 10,
        }
    }
    report = evaluate(make_run(base), make_run(cand), make_policy(metrics=metrics))
    assert report.status == "FAIL"
    assert failed(report) == {"metrics.latency.max_mean_regression_abs"}
    assert {d.code for d in report.diagnostics} == {"pct_undefined"}


def test_categories_and_determinism() -> None:
    base = [case(i, metadata={"category": "x" if i < "c" else "y"}) for i in "dcba"]
    cand = [case(i, 0.5, metadata={"category": "x" if i < "c" else "y"}) for i in "abcd"]
    first = evaluate(make_run(base), make_run(cand), make_policy())
    second = evaluate(make_run(base[::-1]), make_run(cand[::-1]), make_policy())
    assert first.model_dump_json() == second.model_dump_json()
    assert first.comparison is not None
    assert [(c.category, c.metric, c.cases) for c in first.comparison.categories] == [
        ("x", "latency", 2),
        ("x", "quality", 2),
        ("y", "latency", 2),
        ("y", "quality", 2),
    ]
