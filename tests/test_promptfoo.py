"""promptfoo adapter. Fixtures are real `promptfoo eval -o` files (promptfoo 0.125.1, echo-style
local provider), trimmed of config and local paths."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from evalgate import compare, import_promptfoo
from evalgate.cli import app
from evalgate.io import InputError, load_run

FIXTURES = Path(__file__).parent / "fixtures" / "promptfoo"
MANIFEST = {"dataset": "support", "dataset_version": "v1", "evaluator_version": "pf-asserts-1"}


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def write(tmp_path: Path, data: Any, name: str = "results.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = data["results"]["results"]
    return result


def codes(exc: pytest.ExceptionInfo[InputError]) -> list[str]:
    return [d.code for d in exc.value.diagnostics]


def test_plain_results_map_to_cases(tmp_path: Path) -> None:
    summary = import_promptfoo(FIXTURES / "plain.json", tmp_path / "run", **MANIFEST)
    assert (summary.cases, summary.repeats) == (3, 1)
    run = load_run(tmp_path / "run")
    assert run.manifest.run_id == fixture("plain")["evalId"]
    cases = {c.case_id: c for c in run.cases}
    assert [c.case_id for c in run.cases] == ["crash", "greeting", "refund policy"]
    refund = cases["refund policy"]
    assert refund.scores == {"score": 0.635, "mentions_refund": 1.0, "quality": 0.27}
    assert refund.checks == {"pass": True, "mentions_refund": True, "quality": True}
    assert refund.metrics == {"latency_ms": 9.0, "cost": 0.001, "tokens": 27.0}
    assert refund.metadata == {"category": "billing"}
    greeting = cases["greeting"]  # assertion failure: an ok case with failing checks
    assert greeting.status == "ok" and greeting.checks["pass"] is False
    assert greeting.checks["mentions_refund"] is False
    crash = cases["crash"]  # provider error; its message (with a stack trace) is not copied
    assert crash.status == "error" and crash.error is not None
    assert "exploded" not in crash.error


def test_prompts_vars_and_outputs_are_never_copied(tmp_path: Path) -> None:
    import_promptfoo(FIXTURES / "plain.json", tmp_path / "run", **MANIFEST)
    text = (tmp_path / "run" / "cases.jsonl").read_text(encoding="utf-8")
    for secret in ("refunds take 5 days", "hello there", "Answer:", "callApi"):
        assert secret not in text


def test_repeats_become_repeated_runs(tmp_path: Path) -> None:
    summary = import_promptfoo(FIXTURES / "repeat.json", tmp_path / "run", **MANIFEST)
    assert (summary.cases, summary.repeats) == (3, 2)
    run = load_run(tmp_path / "run")
    assert run.manifest.repeats == 2
    assert sorted((c.case_id, c.repeat) for c in run.cases)[:2] == [("crash", 0), ("crash", 1)]


def test_end_to_end_compare_catches_a_new_failure(tmp_path: Path) -> None:
    data = fixture("plain")
    refund = next(r for r in rows(data) if r["testCase"]["description"] == "refund policy")
    refund["success"] = False
    refund["gradingResult"]["componentResults"][0]["pass"] = False
    import_promptfoo(FIXTURES / "plain.json", tmp_path / "base", **MANIFEST, run_id="base")
    import_promptfoo(write(tmp_path, data), tmp_path / "cand", **MANIFEST, run_id="cand")
    policy = tmp_path / "evalgate.yaml"
    policy.write_text(
        "version: 1\nchecks:\n  max_new_failures: 0\nmax_execution_errors: 1\n", encoding="utf-8"
    )
    report = compare(tmp_path / "base", tmp_path / "cand", policy=policy)
    assert report.status == "FAIL"
    assert {(c.case_id, c.check) for c in report.comparison.newly_failing_checks} == {
        ("refund policy", "pass"),
        ("refund policy", "mentions_refund"),
    }


def test_several_providers_need_a_choice(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="model-b.*model-a") as exc:
        import_promptfoo(FIXTURES / "multi.json", tmp_path / "x", **MANIFEST)
    assert codes(exc) == ["import_select"]
    assert not (tmp_path / "x").exists()
    for choice in ("model-b", "echo"):  # label or id
        out = tmp_path / choice
        import_promptfoo(FIXTURES / "multi.json", out, **MANIFEST, provider=choice)
        assert load_run(out).cases[0].metrics["cost"] == 0.0
    with pytest.raises(InputError, match="nothing matches"):
        import_promptfoo(FIXTURES / "multi.json", tmp_path / "y", **MANIFEST, provider="nope")


def test_bare_summary_without_wrapper_accepted(tmp_path: Path) -> None:
    src = write(tmp_path, fixture("plain")["results"])
    import_promptfoo(src, tmp_path / "run", **MANIFEST, run_id="r")
    assert len(load_run(tmp_path / "run").cases) == 3
    with pytest.raises(InputError, match="--run-id"):  # no evalId to default to
        import_promptfoo(src, tmp_path / "run2", **MANIFEST)


@pytest.mark.parametrize("version", [2, "3", True, None])
def test_other_format_versions_rejected(tmp_path: Path, version: Any) -> None:
    data = fixture("plain")
    data["results"]["version"] = version
    with pytest.raises(InputError) as exc:
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)
    assert codes(exc) == ["import_format"]


def test_missing_and_duplicate_descriptions(tmp_path: Path) -> None:
    data = fixture("plain")
    del rows(data)[0]["testCase"]["description"]
    with pytest.raises(InputError, match="give every test a description"):
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)

    data = fixture("plain")
    for r in rows(data):
        r["testCase"]["description"] = "same"
    with pytest.raises(InputError) as exc:
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)
    assert codes(exc) == ["duplicate_case_id"]
    # A var that is unique per test works as the case id instead.
    import_promptfoo(write(tmp_path, data), tmp_path / "v", **MANIFEST, case_id="vars.q")
    assert "BOOM" in {c.case_id for c in load_run(tmp_path / "v").cases}


def test_bad_case_id_field_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        import_promptfoo(FIXTURES / "plain.json", tmp_path / "x", **MANIFEST, case_id="testIdx")
    assert codes(exc) == ["import_case_id"]


def test_grader_error_is_an_error_case(tmp_path: Path) -> None:
    data = fixture("plain")
    refund = next(r for r in rows(data) if r["testCase"]["description"] == "refund policy")
    refund["gradingResult"]["componentResults"][1]["metadata"] = {"graderError": True}
    import_promptfoo(write(tmp_path, data), tmp_path / "run", **MANIFEST)
    case = next(c for c in load_run(tmp_path / "run").cases if c.case_id == "refund policy")
    assert case.status == "error"


def test_cached_responses_have_no_latency_or_cost(tmp_path: Path) -> None:
    data = fixture("plain")
    for r in rows(data):
        r.setdefault("response", {})["cached"] = True
    import_promptfoo(write(tmp_path, data), tmp_path / "run", **MANIFEST)
    assert all(c.metrics == {} for c in load_run(tmp_path / "run").cases)


def test_bad_values_reported_and_nothing_written(tmp_path: Path) -> None:
    data = fixture("plain")
    good = [r for r in rows(data) if r.get("failureReason") != 2]
    good[0]["namedScores"]["score"] = 1
    good[1]["success"] = "yes"
    with pytest.raises(InputError) as exc:
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)
    messages = " | ".join(d.message for d in exc.value.diagnostics)
    assert "clashes with the overall score" in messages and "success must be" in messages
    assert not (tmp_path / "x" / "run.json").exists()


def test_mixed_repeat_rows_rejected(tmp_path: Path) -> None:
    data = fixture("repeat")
    del rows(data)[0]["repeatIndex"]
    with pytest.raises(InputError, match="some records have a repeat"):
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)


def test_cli_from_promptfoo(tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["import", str(FIXTURES / "repeat.json"), "--out", str(tmp_path / "run")]
    flags = ["--dataset", "d", "--dataset-version", "1", "--evaluator-version", "e"]
    result = runner.invoke(app, [*base, "--from", "promptfoo", *flags])
    assert result.exit_code == 0, result.output
    assert "3 cases x 2 repeats" in result.output
    for args, message in (
        (["--from", "promptfoo"], "needs --dataset"),
        (["--from", "langfuse", *flags], "unknown --from"),
        (["--from", "promptfoo", "--mapping", "m.yaml", *flags], "exclusive"),
        (["--mapping", "m.yaml", "--provider", "x"], "only apply with --from"),
        ([], "pass --mapping"),
    ):
        result = runner.invoke(app, [*base, "--overwrite", *args])
        assert result.exit_code == 2 and message in result.output.replace("\n", " "), args


# Review findings (PR #16).


@pytest.mark.parametrize("reason", [None, 3, "2", True])
def test_unknown_failure_reason_rejected_not_guessed(tmp_path: Path, reason: Any) -> None:
    data = fixture("plain")
    crash = next(r for r in rows(data) if r["failureReason"] == 2)
    if reason is None:
        del crash["failureReason"]
    else:
        crash["failureReason"] = reason
    with pytest.raises(InputError, match="failureReason must be 0, 1 or 2"):
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)


def test_error_messages_never_copied(tmp_path: Path) -> None:
    data = fixture("plain")
    crash = next(r for r in rows(data) if r["failureReason"] == 2)
    crash["error"] = "API said no for prompt: Answer: secret at C:/Users/me/keys.txt"
    refund = next(r for r in rows(data) if r["testCase"]["description"] == "refund policy")
    refund["gradingResult"]["metadata"] = {"graderError": True}
    refund["error"] = "grader down at /home/me/.env"
    import_promptfoo(write(tmp_path, data), tmp_path / "run", **MANIFEST)
    text = (tmp_path / "run" / "cases.jsonl").read_text(encoding="utf-8")
    assert "secret" not in text and "keys.txt" not in text and ".env" not in text
    assert "promptfoo grader error" in text


def test_selection_messages_hide_prompt_text_and_resolve_ambiguity(tmp_path: Path) -> None:
    data = fixture("multi")
    a, b = rows(data)  # a: id flaky-echo label model-a; b: id echo label model-b
    b["provider"]["label"] = "flaky-echo"  # b's label is a's id
    for r in (a, b):
        r["prompt"]["label"] = "p.txt: SECRET SYSTEM PROMPT {{q}}"
    src = write(tmp_path, data)
    with pytest.raises(InputError) as exc:
        import_promptfoo(src, tmp_path / "x", **MANIFEST)
    assert "SECRET" not in exc.value.diagnostics[0].message
    # Label wins: "flaky-echo" picks b (labelled so), "model-a" picks a.
    import_promptfoo(src, tmp_path / "b", **MANIFEST, provider="flaky-echo")
    assert load_run(tmp_path / "b").cases[0].metrics["cost"] == 0.0
    import_promptfoo(src, tmp_path / "a", **MANIFEST, provider="model-a")
    assert load_run(tmp_path / "a").cases[0].metrics["cost"] == 0.001


def test_same_label_providers_picked_by_id(tmp_path: Path) -> None:
    data = fixture("multi")
    for r in rows(data):
        r["provider"]["label"] = "m"
    src = write(tmp_path, data)
    with pytest.raises(InputError) as exc:
        import_promptfoo(src, tmp_path / "x", **MANIFEST, provider="m")
    assert codes(exc) == ["import_select"]
    import_promptfoo(src, tmp_path / "e", **MANIFEST, provider="echo")
    assert load_run(tmp_path / "e").cases[0].metrics["cost"] == 0.0


def test_prompt_selected_by_id_prefix(tmp_path: Path) -> None:
    data = fixture("multi")
    a, b = rows(data)
    b["provider"] = dict(a["provider"])
    b["promptId"] = "f" * 64
    src = write(tmp_path, data)
    with pytest.raises(InputError, match="several provider/prompt"):
        import_promptfoo(src, tmp_path / "x", **MANIFEST)
    import_promptfoo(src, tmp_path / "p", **MANIFEST, prompt="ffffffff")
    assert load_run(tmp_path / "p").cases[0].metrics["cost"] == 0.0
    with pytest.raises(InputError, match="nothing matches"):  # too short to be a prefix
        import_promptfoo(src, tmp_path / "q", **MANIFEST, prompt="fff")


def test_metadata_case_id_int_ids_and_non_finite_metadata(tmp_path: Path) -> None:
    data = fixture("plain")
    for n, r in enumerate(rows(data)):
        r["testCase"]["metadata"] |= {"ticket": 100 + n, "weight": float("nan")}
    import_promptfoo(write(tmp_path, data), tmp_path / "run", **MANIFEST, case_id="metadata.ticket")
    cases = load_run(tmp_path / "run").cases
    assert sorted(c.case_id for c in cases) == ["100", "101", "102"]
    assert all("weight" not in c.metadata for c in cases)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(provider="echo"),
        lambda r: r["testCase"].update(metadata=["x"]),
        lambda r: r.update(namedScores=[1]),
        lambda r: r["gradingResult"].update(componentResults=["x"]),
    ],
)
def test_malformed_rows_are_input_errors(tmp_path: Path, mutate: Any) -> None:
    data = fixture("plain")
    mutate(next(r for r in rows(data) if r["testCase"]["description"] == "refund policy"))
    with pytest.raises(InputError):
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)


def test_negative_latency_rejected(tmp_path: Path) -> None:
    data = fixture("plain")
    next(r for r in rows(data) if r["failureReason"] != 2)["latencyMs"] = -5
    with pytest.raises(InputError, match="must not be negative: latency_ms"):
        import_promptfoo(write(tmp_path, data), tmp_path / "x", **MANIFEST)
