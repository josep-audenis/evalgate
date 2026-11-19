"""Pure history summaries and entry models (phase-7 §2, 0.10.0 T1)."""

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import MANIFEST
from pydantic import TypeAdapter, ValidationError

import evalgate
from evalgate import EvalRun
from evalgate.comparison.history import summarize_report, summarize_run
from evalgate.io import load_run
from evalgate.models.history import HistoryEntry
from evalgate.models.run import CaseResult, Run, RunManifest

EXAMPLES = Path(__file__).parent.parent / "examples"
SRC = Path(evalgate.__file__).parent
SECRET = "SENTINEL-hist-3e7a"
ENTRY = TypeAdapter(HistoryEntry)
META = {"history_version": 1, "id": 1, "recorded_at": "2026-11-19T12:00:00Z"}


def run(*cases: dict[str, Any], **manifest: Any) -> Run:
    return Run(
        manifest=RunManifest(**(MANIFEST | manifest)),
        cases=[CaseResult(**({"status": "ok"} | c)) for c in cases],
    )


# --- run summaries ---------------------------------------------------------------------------


def test_run_summary_hand_computed() -> None:
    r = run(
        {"case_id": "a", "scores": {"q": 1}, "metrics": {"ms": 10}, "checks": {"ok": True}},
        {"case_id": "b", "scores": {"q": 3}, "checks": {"ok": False, "x": True}},
        {"case_id": "c", "status": "error", "error": "boom", "scores": {"q": 100}},
        application="app",
        application_version="v2",
        extra_field="not stored",
    )
    s = summarize_run(r)
    assert s["run"] == {
        "run_id": "run",
        "application": "app",
        "application_version": "v2",
        "dataset_id": "suite",
        "dataset_version": "v1",
    }
    assert (s["cases"], s["errors"]) == (3, 1)
    assert s["means"] == {
        "metrics.ms": {"value": 10.0, "cases": 1},  # sparse: own count
        "scores.q": {"value": 2.0, "cases": 2},  # errored case excluded
    }
    assert s["checks"] == {"ok": {"value": 0.5, "cases": 2}, "x": {"value": 1.0, "cases": 1}}
    assert s["failing_checks"] == [{"case_id": "b", "check": "ok"}]
    ENTRY.validate_python(META | s)


def test_run_summary_collapses_repeats() -> None:
    r = run(
        {"case_id": "a", "repeat": 0, "scores": {"q": 1}, "checks": {"ok": True}},
        {"case_id": "a", "repeat": 1, "scores": {"q": 3}, "checks": {"ok": False}},
        {"case_id": "b", "repeat": 0, "scores": {"q": 4}, "checks": {"ok": True}},
        {"case_id": "b", "repeat": 1, "scores": {"q": 4}, "checks": {"ok": True}},
        schema_version="1.1",
    )
    s = summarize_run(r)
    assert s["cases"] == 2  # collapsed, as `evalgate validate` counts
    assert s["means"]["scores.q"] == {"value": 3.0, "cases": 2}  # mean(2, 4)
    assert s["checks"]["ok"] == {"value": 0.5, "cases": 2}  # a fails: one repeat failed
    assert s["failing_checks"] == [{"case_id": "a", "check": "ok"}]


def test_failing_pairs_are_sorted() -> None:
    r = run(
        {"case_id": "b", "checks": {"z": False, "a": False}},
        {"case_id": "a", "checks": {"m": False}},
    )
    pairs = [(f["case_id"], f["check"]) for f in summarize_run(r)["failing_checks"]]
    assert pairs == [("a", "m"), ("b", "a"), ("b", "z")]


def test_non_finite_mean_raises() -> None:
    r = run({"case_id": "a", "scores": {"q": 1e308}}, {"case_id": "b", "scores": {"q": 1e308}})
    with pytest.raises(ValueError):
        summarize_run(r)


def test_run_summary_holds_no_outputs(tmp_path: Path) -> None:
    with EvalRun(
        "r",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=tmp_path,
        store_output=True,
    ) as er:
        er.record("a", output=SECRET, scores={"q": 1})
    assert SECRET not in repr(summarize_run(load_run(tmp_path)))


# --- compare summaries -----------------------------------------------------------------------


def test_compare_summary_of_a_fail() -> None:
    d = EXAMPLES / "quickstart"
    report = evalgate.compare(d / "baseline", d / "candidate", d / "evalgate.yaml")
    s = summarize_report(report)
    assert s["status"] == "FAIL"
    assert s["baseline"]["run_id"] and s["candidate"]["run_id"]
    assert [m["path"] for m in s["metrics"]] == sorted(m["path"] for m in s["metrics"])
    assert s["failing_checks"] == sorted(
        s["failing_checks"], key=lambda f: (f["case_id"], f["check"])
    )
    assert s["failing_checks"]
    assert report.comparison is not None
    m = report.comparison.metrics[0]
    row = next(r for r in s["metrics"] if r["path"] == m.path)
    assert (row["baseline_mean"], row["candidate_mean"], row["delta_abs"], row["cases"]) == (
        m.baseline_mean,
        m.candidate_mean,
        m.delta_abs,
        m.cases,
    )
    ENTRY.validate_python(META | s | {"report_sha256": "0" * 64})


def test_invalid_on_load_has_null_identities(tmp_path: Path) -> None:
    report = evalgate.compare(tmp_path / "nope", tmp_path / "nope2", tmp_path / "p.yaml")
    s = summarize_report(report)
    assert (s["status"], s["baseline"], s["candidate"], s["metrics"]) == ("INVALID", None, None, [])
    ENTRY.validate_python(META | s | {"report_sha256": "0" * 64})


def test_two_policy_metrics_on_one_path_give_one_row(tmp_path: Path) -> None:
    d = EXAMPLES / "quickstart"
    policy = (
        (d / "evalgate.yaml")
        .read_text()
        .replace(
            "metrics:\n",
            "metrics:\n  quality_again:\n    path: scores.quality\n"
            "    direction: maximize\n    max_mean_regression_abs: 1\n",
            1,
        )
    )
    (tmp_path / "p.yaml").write_text(policy)
    report = evalgate.compare(d / "baseline", d / "candidate", tmp_path / "p.yaml")
    assert report.comparison is not None
    paths = [m.path for m in report.comparison.metrics]
    assert paths.count("scores.quality") == 2
    assert [m["path"] for m in summarize_report(report)["metrics"]].count("scores.quality") == 1


# --- entry model -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"history_version": 3},
        {"id": 0},
        {"recorded_at": "2026-11-19 12:00"},
        {"recorded_at": "2026-13-45T99:99:99Z"},
        {"run": {"run_id": "", "dataset_id": "d", "dataset_version": "v"}},
        {"label": "x" * 201},
        {"kind": "other"},
        {"surprise": 1},
        {"cases": True},
    ],
)
def test_entry_model_is_strict(change: dict[str, Any]) -> None:
    good = META | summarize_run(run({"case_id": "a", "scores": {"q": 1}}))
    ENTRY.validate_python(good)
    with pytest.raises(ValidationError):
        ENTRY.validate_python(good | change)


# --- purity ----------------------------------------------------------------------------------


def imported(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_history_summaries_are_pure() -> None:
    assert not any(m.startswith("evalgate.io") for m in imported(SRC / "comparison" / "history.py"))


ENGINE = [
    SRC / "engine.py",
    SRC / "api.py",
    SRC / "validation.py",
    *(p for p in (SRC / "comparison").glob("*.py") if p.name != "history.py"),
    *(SRC / "policies").glob("*.py"),
]


@pytest.mark.parametrize("path", ENGINE, ids=lambda p: p.name)
def test_engine_never_references_history(path: Path) -> None:
    assert not any("history" in m for m in imported(path))


def write_run(path: Path, cases: list[dict[str, Any]]) -> Path:
    path.mkdir(parents=True)
    manifest = MANIFEST | {"run_id": path.name}
    (path / "run.json").write_text(json.dumps(manifest))
    (path / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    return path


def test_failing_pairs_cover_compared_cases_only(tmp_path: Path) -> None:
    base = write_run(
        tmp_path / "b",
        [
            {"case_id": "a", "status": "ok", "scores": {"q": 1}, "checks": {"c": True}},
            {"case_id": "b", "status": "error", "error": "x"},
        ],
    )
    cand = write_run(
        tmp_path / "c",
        [
            {"case_id": "a", "status": "ok", "scores": {"q": 1}, "checks": {"c": False}},
            {"case_id": "b", "status": "ok", "scores": {"q": 1}, "checks": {"c": False}},
        ],
    )
    policy = tmp_path / "p.yaml"
    policy.write_text(
        "version: 1\ncomparison: {min_compared_cases: 1}\n"
        "max_execution_errors: 5\n"
        "metrics: {q: {path: scores.q, direction: maximize, max_mean_regression_abs: 1}}\n"
    )
    report = evalgate.compare(base, cand, policy)
    assert report.status != "INVALID", report.diagnostics
    assert summarize_report(report)["failing_checks"] == [{"case_id": "a", "check": "c"}]


def test_invalid_with_comparison_has_no_rows(tmp_path: Path) -> None:
    base = write_run(tmp_path / "b", [{"case_id": "a", "status": "ok", "scores": {"q": 0}}])
    cand = write_run(
        tmp_path / "c",
        [{"case_id": "a", "status": "ok", "scores": {"q": 0}, "checks": {"x": False}}],
    )
    policy = tmp_path / "p.yaml"
    policy.write_text(
        "version: 1\ncomparison: {min_compared_cases: 1}\n"
        "metrics: {q: {path: scores.q, direction: maximize, max_mean_regression_pct: 5}}\n"
    )
    report = evalgate.compare(base, cand, policy)
    assert report.status == "INVALID" and report.comparison is not None
    s = summarize_report(report)
    assert (s["metrics"], s["failing_checks"]) == ([], [])
