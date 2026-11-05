"""DeepEval adapter. Fixtures are real `evaluate(..., results_folder=...)` files from deepeval
4.3.3 with self-coded deterministic metrics; the only edit is renaming DeepEval's default
`test_case_3` (which the importer rejects) to "maybe refund"."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from evalgate import compare, import_deepeval
from evalgate.cli import app
from evalgate.io import InputError, load_run

FIXTURES = Path(__file__).parent / "fixtures" / "deepeval"
MANIFEST = {"dataset": "support", "dataset_version": "v1", "evaluator_version": "de-metrics-1"}
KEYWORD = "Keyword[refunds]"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def write(tmp_path: Path, data: Any, name: str = "test_run.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def case(data: dict[str, Any], name: str) -> dict[str, Any]:
    found: dict[str, Any] = next(c for c in data["testCases"] if c["name"] == name)
    return found


def codes(exc: pytest.ExceptionInfo[InputError]) -> list[str]:
    return [d.code for d in exc.value.diagnostics]


def test_test_cases_map_to_cases(tmp_path: Path) -> None:
    summary = import_deepeval(FIXTURES / "baseline.json", tmp_path / "run", **MANIFEST)
    assert (summary.cases, summary.repeats) == (4, 1)
    run = load_run(tmp_path / "run")
    assert run.manifest.run_id == "baseline"  # file name when there is no identifier
    cases = {c.case_id: c for c in run.cases}
    refund = cases["refund policy"]
    assert refund.scores == {KEYWORD: 1.0, "Length": 0.19}
    assert refund.checks == {KEYWORD: True, "Length": True, "pass": True}
    assert refund.metrics == {"latency_ms": 420.0}
    assert refund.metadata == {"category": "billing"}
    assert cases["greeting"].checks["pass"] is False  # a failed metric is an ok case
    crash = cases["crash"]  # an errored metric makes the case an error; message not copied
    assert crash.status == "error" and crash.error is not None and "secret" not in crash.error


def test_inputs_outputs_and_reasons_never_copied(tmp_path: Path) -> None:
    import_deepeval(FIXTURES / "baseline.json", tmp_path / "run", **MANIFEST)
    text = (tmp_path / "run" / "cases.jsonl").read_text(encoding="utf-8")
    for secret in ("refund?", "refunds take 5 days", "hello there", "looked for", "BOOM"):
        assert secret not in text


def test_end_to_end_compare(tmp_path: Path) -> None:
    import_deepeval(FIXTURES / "baseline.json", tmp_path / "b", **MANIFEST)
    import_deepeval(FIXTURES / "candidate.json", tmp_path / "c", **MANIFEST)
    policy = tmp_path / "evalgate.yaml"
    policy.write_text(
        "version: 1\nchecks:\n  max_new_failures: 0\nmax_execution_errors: 1\n", encoding="utf-8"
    )
    report = compare(tmp_path / "b", tmp_path / "c", policy=policy)
    assert report.status == "FAIL"
    assert report.comparison is not None
    flips = {(c.case_id, c.check) for c in report.comparison.newly_failing_checks}
    assert flips == {("maybe refund", KEYWORD), ("maybe refund", "pass")}


def test_conversational_test_cases(tmp_path: Path) -> None:
    import_deepeval(FIXTURES / "conversational.json", tmp_path / "run", **MANIFEST)
    (chat,) = load_run(tmp_path / "run").cases
    assert (chat.case_id, chat.scores, chat.checks) == (
        "support chat",
        {"Turns": 0.5},
        {"Turns": True, "pass": True},
    )
    assert "charged twice" not in (tmp_path / "run" / "cases.jsonl").read_text(encoding="utf-8")


def test_default_names_rejected_metadata_case_id_works(tmp_path: Path) -> None:
    data = fixture("baseline")
    case(data, "maybe refund")["name"] = "test_case_3"
    with pytest.raises(InputError, match="position-based test_case_<n>"):
        import_deepeval(write(tmp_path, data), tmp_path / "x", **MANIFEST)
    for n, c in enumerate(data["testCases"]):
        c["metadata"] = {"ticket": 10 + n}
    import_deepeval(write(tmp_path, data), tmp_path / "m", **MANIFEST, case_id="metadata.ticket")
    assert sorted(c.case_id for c in load_run(tmp_path / "m").cases) == ["10", "11", "12", "13"]


def test_duplicate_names_rejected(tmp_path: Path) -> None:
    data = fixture("baseline")
    for c in data["testCases"]:
        c["name"] = "same"
    with pytest.raises(InputError) as exc:
        import_deepeval(write(tmp_path, data), tmp_path / "x", **MANIFEST)
    assert codes(exc) == ["duplicate_case_id"]
    assert not (tmp_path / "x").exists()


def test_identifier_wrapper_and_run_id(tmp_path: Path) -> None:
    data = fixture("baseline") | {"identifier": "nightly-42"}
    import_deepeval(write(tmp_path, {"testRunData": data}), tmp_path / "a", **MANIFEST)
    assert load_run(tmp_path / "a").manifest.run_id == "nightly-42"
    import_deepeval(FIXTURES / "baseline.json", tmp_path / "b", **MANIFEST, run_id="mine")
    assert load_run(tmp_path / "b").manifest.run_id == "mine"


@pytest.mark.parametrize(
    "data",
    [{"testRunLink": "https://app.confident-ai.com/x"}, [], {"testCases": "x"}, {"testCases": []}],
)
def test_not_a_test_run_rejected(tmp_path: Path, data: Any) -> None:
    with pytest.raises(InputError):
        import_deepeval(write(tmp_path, data), tmp_path / "x", **MANIFEST)


def test_metric_shapes_and_values(tmp_path: Path) -> None:
    data = fixture("baseline")
    refund = case(data, "refund policy")
    refund["metricsData"][1]["name"] = "a.b"
    greeting = case(data, "greeting")
    greeting["metricsData"][0]["score"] = "high"
    greeting["completionTime"] = -1
    with pytest.raises(InputError) as exc:
        import_deepeval(write(tmp_path, data), tmp_path / "x", **MANIFEST)
    messages = " | ".join(d.message for d in exc.value.diagnostics)
    assert "without dots" in messages and "finite number" in messages
    assert "must not be negative: latency_ms" in messages


def test_metric_without_threshold_gives_score_only_and_tokens(tmp_path: Path) -> None:
    data = fixture("baseline")
    refund = case(data, "refund policy")
    del refund["metricsData"][1]["threshold"]
    refund |= {"inputTokenCount": 12, "outputTokenCount": 30, "tokenCost": 0.002}
    import_deepeval(write(tmp_path, data), tmp_path / "run", **MANIFEST)
    got = next(c for c in load_run(tmp_path / "run").cases if c.case_id == "refund policy")
    assert "Length" in got.scores and "Length" not in got.checks
    assert got.metrics == {"latency_ms": 420.0, "cost": 0.002, "tokens": 42.0}


def test_malformed_test_case_is_input_error(tmp_path: Path) -> None:
    data = fixture("baseline")
    case(data, "greeting")["metricsData"] = "x"
    case(data, "refund policy")["metadata"] = ["x"]
    with pytest.raises(InputError):
        import_deepeval(write(tmp_path, data), tmp_path / "x", **MANIFEST)


def test_cli_from_deepeval(tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["import", str(FIXTURES / "baseline.json"), "--out", str(tmp_path / "run")]
    flags = ["--dataset", "d", "--dataset-version", "1", "--evaluator-version", "e"]
    result = runner.invoke(app, [*base, "--from", "deepeval", *flags])
    assert result.exit_code == 0, result.output
    assert "4 cases" in result.output
    result = runner.invoke(app, [*base, "--from", "deepeval", *flags, "--provider", "x"])
    assert result.exit_code == 2 and "only apply with --from promptfoo" in result.output
    result = runner.invoke(app, [*base, "--from", "deepeval", "--overwrite", *flags[:2]])
    assert result.exit_code == 2 and "--from deepeval needs" in result.output
