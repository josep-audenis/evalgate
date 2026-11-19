"""Bundled examples must keep producing the outcome the docs describe."""

import json
import runpy
import shutil
from pathlib import Path

import pytest

import evalgate

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize(
    ("name", "failed_rules"),
    [
        ("quickstart", {"checks.required.safety_valid", "checks.max_new_failures"}),
        (
            "cost-latency",
            {"metrics.cost.max_mean_regression_pct", "metrics.latency.max_mean_regression_pct"},
        ),
        ("repeats", {"checks.required.safety_valid", "checks.max_new_failures"}),
        (
            "native-evaluators",
            {
                "checks.required.is_json",
                "checks.required.answer_shape",
                "checks.max_new_failures",
            },
        ),
        ("judge", {"checks.required.faithful", "checks.max_new_failures"}),
    ],
)
def test_example_fails_for_documented_reason(name: str, failed_rules: set[str]) -> None:
    d = EXAMPLES / name
    report = evalgate.compare(d / "baseline", d / "candidate", d / "evalgate.yaml")
    assert report.status == "FAIL"
    assert {r.rule for r in report.rules if not r.passed} == failed_rules
    assert report.comparison is not None
    quality = next(m for m in report.comparison.metrics if m.name == "quality")
    assert quality.delta_abs > 0  # every example improves average quality yet must not ship


@pytest.mark.parametrize(
    "name", ["quickstart", "cost-latency", "repeats", "native-evaluators", "judge"]
)
def test_example_baseline_against_itself_passes(name: str) -> None:
    d = EXAMPLES / name
    assert evalgate.compare(d / "baseline", d / "baseline", d / "evalgate.yaml").status == "PASS"


def test_native_evaluators_example_matches_its_generator(tmp_path: Path) -> None:
    # The committed run dirs must be exactly what make_runs.py writes (same evaluator versions).
    src = EXAMPLES / "native-evaluators"
    shutil.copy(src / "make_runs.py", tmp_path / "make_runs.py")
    runpy.run_path(str(tmp_path / "make_runs.py"))  # in-process: uses the evalgate under test
    for name in ("outputs-baseline.jsonl", "outputs-candidate.jsonl"):
        assert (tmp_path / name).read_text() == (src / name).read_text()
    for run in ("baseline", "candidate"):
        assert json.loads((tmp_path / run / "run.json").read_text()) == json.loads(
            (src / run / "run.json").read_text()
        )
        got = [json.loads(x) for x in (tmp_path / run / "cases.jsonl").read_text().splitlines()]
        want = [json.loads(x) for x in (src / run / "cases.jsonl").read_text().splitlines()]
        assert got == want


def test_native_evaluators_example_via_cli_config(tmp_path: Path) -> None:
    # The no-Python path (outputs-*.jsonl + evaluators.yaml) reaches the same decision.
    pytest.importorskip("jsonschema")
    d = EXAMPLES / "native-evaluators"
    for run in ("baseline", "candidate"):
        evalgate.evaluate_outputs(
            d / f"outputs-{run}.jsonl", d / "evaluators.yaml", tmp_path / run, run_id=run
        )
    report = evalgate.compare(tmp_path / "baseline", tmp_path / "candidate", d / "evalgate.yaml")
    assert report.status == "FAIL"
    assert {r.rule for r in report.rules if not r.passed} == {
        "checks.required.is_json",
        "checks.required.answer_shape",
        "checks.max_new_failures",
    }


def test_judge_example_matches_its_generator(tmp_path: Path) -> None:
    # Runs and the committed calibration.json are exactly what make_runs.py writes, offline.
    src = EXAMPLES / "judge"
    for name in ("make_runs.py", "labels.jsonl"):
        shutil.copy(src / name, tmp_path / name)
    runpy.run_path(str(tmp_path / "make_runs.py"))
    for run in ("baseline", "candidate"):
        for name in ("run.json", "cases.jsonl"):
            assert (tmp_path / run / name).read_text() == (src / run / name).read_text()
    assert (tmp_path / "calibration.json").read_text() == (src / "calibration.json").read_text()


def test_history_example_builds_its_store(tmp_path: Path) -> None:
    (tmp_path / "history").mkdir()
    shutil.copy(EXAMPLES / "history" / "make_store.py", tmp_path / "history" / "make_store.py")
    shutil.copytree(EXAMPLES / "quickstart", tmp_path / "quickstart")
    runpy.run_path(str(tmp_path / "history" / "make_store.py"))
    entries = evalgate.read_history(tmp_path / "history" / "store")
    assert [(e.id, e.kind, e.label) for e in entries] == [
        (1, "run", "v1.4.0"),
        (2, "run", "v1.5.0"),
        (3, "compare", "v1.5.0 gate"),
    ]
    assert getattr(entries[2], "status", None) == "FAIL"
    assert entries[0].recorded_at == "2026-11-01T09:00:00Z"
