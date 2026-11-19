"""Bundled examples must keep producing the outcome the docs describe."""

import json
import shutil
import subprocess
import sys
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


@pytest.mark.parametrize("name", ["quickstart", "cost-latency", "repeats", "native-evaluators"])
def test_example_baseline_against_itself_passes(name: str) -> None:
    d = EXAMPLES / name
    assert evalgate.compare(d / "baseline", d / "baseline", d / "evalgate.yaml").status == "PASS"


def test_native_evaluators_example_matches_its_generator(tmp_path: Path) -> None:
    # The committed run dirs must be exactly what make_runs.py writes (same evaluator versions).
    src = EXAMPLES / "native-evaluators"
    shutil.copy(src / "make_runs.py", tmp_path / "make_runs.py")
    subprocess.run([sys.executable, str(tmp_path / "make_runs.py")], check=True)
    for run in ("baseline", "candidate"):
        assert json.loads((tmp_path / run / "run.json").read_text()) == json.loads(
            (src / run / "run.json").read_text()
        )
        got = [json.loads(x) for x in (tmp_path / run / "cases.jsonl").read_text().splitlines()]
        want = [json.loads(x) for x in (src / run / "cases.jsonl").read_text().splitlines()]
        assert got == want
