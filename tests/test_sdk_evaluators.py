"""EvalRun with native evaluators and store_output (native-evaluators design §3, T5)."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import POLICY

import evalgate
from evalgate import EvalRun
from evalgate.evaluators import Evaluator, check, score
from evalgate.io import load_run

SECRET = "SENTINEL-7f3a-output"
SECRET_CASE = "SENTINEL-9c1d-prompt"


def short(version: str = "1") -> Evaluator:
    """Stands in for a built-in such as length(max=...): a changed argument changes `version`."""
    return Evaluator("short", version, "check", lambda o, c: len(o) < 100)


@check(name="polite", version="1")
def polite(output: Any, case: Any) -> bool:
    return "please" in output


@score(name="brevity", version="1")
def brevity(output: Any, case: Any) -> float:
    return 1.0 / (1 + len(output))


@check(name="boom", version="1")
def boom(output: Any, case: Any) -> bool:
    raise RuntimeError(f"cannot handle {output}")


def new_run(path: Path, run_id: str = "r", **kwargs: Any) -> EvalRun:
    return EvalRun(
        run_id, dataset="d", dataset_version="v", evaluator_version="e", path=path, **kwargs
    )


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (path / "cases.jsonl").read_text().splitlines()]


def manifest(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((path / "run.json").read_text())
    return result


# --- manifest ------------------------------------------------------------------------------


def test_evaluators_are_declared_sorted_with_schema_1_2(tmp_path: Path) -> None:
    with new_run(tmp_path, evaluators=[polite, brevity]) as r:
        r.record("a", output="please")
    m = manifest(tmp_path)
    assert m["schema_version"] == "1.2"
    assert list(m["evaluators"].items()) == [("brevity", "1"), ("polite", "1")]


def test_store_output_alone_keeps_schema_1_0(tmp_path: Path) -> None:
    with new_run(tmp_path, store_output=True) as r:
        r.record("a", scores={"q": 1.0}, output="x")
    assert manifest(tmp_path)["schema_version"] == "1.0"
    assert "evaluators" not in manifest(tmp_path)


def test_empty_evaluator_list_declares_nothing(tmp_path: Path) -> None:
    with new_run(tmp_path, evaluators=[]) as r:
        r.record("a", scores={"q": 1.0})
    assert manifest(tmp_path)["schema_version"] == "1.0"


def test_duplicate_names_rejected_at_construction(tmp_path: Path) -> None:
    other = Evaluator("polite", "2", "check", lambda o, c: True)
    with pytest.raises(ValueError, match="duplicate evaluator names: polite"):
        new_run(tmp_path, evaluators=[polite, other])


def test_non_evaluator_rejected(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        new_run(tmp_path, evaluators=[lambda o, c: True])


# --- recording -------------------------------------------------------------------------------


def test_results_join_scores_and_checks(tmp_path: Path) -> None:
    with new_run(tmp_path, evaluators=[polite, brevity]) as r:
        r.record("a", output="please", scores={"q": 0.5}, checks={"safe": True})
        r.record("b", output="no")
    a, b = lines(tmp_path)
    assert a["checks"] == {"safe": True, "polite": True}
    assert a["scores"] == {"q": 0.5, "brevity": pytest.approx(1 / 7)}
    assert b["status"] == "ok" and b["checks"] == {"polite": False}  # bad output fails the check


def test_evaluator_error_records_a_fixed_error_without_output(tmp_path: Path) -> None:
    with new_run(tmp_path, evaluators=[polite, boom], store_output=True) as r:
        r.record("a", output=SECRET, metadata={"category": "x"})
    (a,) = lines(tmp_path)
    assert a == {
        "case_id": "a",
        "status": "error",
        "metadata": {"category": "x"},
        "error": "evaluator boom failed",
    }
    assert SECRET not in (tmp_path / "cases.jsonl").read_text()


def test_output_required_with_evaluators_or_store_output(tmp_path: Path) -> None:
    for kwargs in ({"evaluators": [polite]}, {"store_output": True}):
        path = tmp_path / str(len(kwargs))
        with pytest.raises(ValueError, match="output= is required"), new_run(path, **kwargs) as r:
            r.record("a", scores={"q": 1.0})
        assert not (path / "cases.jsonl").exists()  # the failed run is discarded


def test_output_none_is_a_valid_output(tmp_path: Path) -> None:
    is_null = Evaluator("is_null", "1", "check", lambda o, c: o is None)
    with new_run(tmp_path, evaluators=[is_null]) as r:
        r.record("a", output=None)
    assert lines(tmp_path)[0]["checks"] == {"is_null": True}


def test_name_clash_with_passed_values_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="clash"), new_run(tmp_path, evaluators=[polite]) as r:
        r.record("a", output="please", checks={"polite": False})


def test_same_name_in_other_section_is_not_a_clash(tmp_path: Path) -> None:
    with new_run(tmp_path, evaluators=[polite]) as r:
        r.record("a", output="please", scores={"polite": 0.3})
    assert lines(tmp_path)[0]["scores"] == {"polite": 0.3}


def test_case_is_passed_but_never_written(tmp_path: Path) -> None:
    match = Evaluator("match", "1", "check", lambda o, c: o == c["expected"])
    with new_run(tmp_path, evaluators=[match]) as r:
        r.record("a", output="Paris", case={"expected": "Paris", "prompt": SECRET_CASE})
    assert lines(tmp_path)[0]["checks"] == {"match": True}
    assert SECRET_CASE not in (tmp_path / "cases.jsonl").read_text()


# --- store_output ------------------------------------------------------------------------------


def test_output_not_stored_by_default(tmp_path: Path) -> None:
    with new_run(tmp_path, evaluators=[polite]) as r:
        r.record("a", output=SECRET + " please")
    assert SECRET not in (tmp_path / "cases.jsonl").read_text()


def test_store_output_writes_a_snapshot_taken_before_evaluators(tmp_path: Path) -> None:
    @check(name="mutates", version="1")
    def mutates(output: Any, case: Any) -> bool:
        output["text"] = "changed"
        return True

    with new_run(tmp_path, evaluators=[mutates], store_output=True) as r:
        r.record("a", output={"text": "original", "n": [1, 2]})
    assert lines(tmp_path)[0]["output"] == {"text": "original", "n": [1, 2]}
    assert load_run(tmp_path).cases[0].output == {"text": "original", "n": [1, 2]}


@pytest.mark.parametrize("bad", [float("nan"), {"x": [float("inf")]}, object(), {1: "x"}])
def test_store_output_rejects_non_json(tmp_path: Path, bad: Any) -> None:
    with pytest.raises(ValueError), new_run(tmp_path, store_output=True) as r:
        r.record("a", scores={"q": 1.0}, output=bad)


# --- end to end -------------------------------------------------------------------------------


def write_pair(tmp_path: Path, base_evs: list[Evaluator], cand_evs: list[Evaluator]) -> Path:
    policy = tmp_path / "evalgate.yaml"
    policy.write_text(
        json.dumps(
            POLICY
            | {
                "metrics": {
                    "quality": {
                        "path": "scores.quality",
                        "direction": "maximize",
                        "max_mean_regression_abs": 0.02,
                    }
                },
                "checks": {"required": ["short"], "max_new_failures": 0},
            }
        )
    )
    for name, evs in (("base", base_evs), ("cand", cand_evs)):
        with new_run(tmp_path / name, run_id=name, evaluators=evs, store_output=True) as r:
            for i in range(3):
                r.record(
                    f"c{i}",
                    output=f"{SECRET} {i}",
                    case={"prompt": SECRET_CASE},
                    scores={"quality": 0.9},
                )
    return policy


def test_stored_outputs_never_reach_any_report(tmp_path: Path) -> None:
    evs = [short()]
    policy = write_pair(tmp_path, evs, evs)
    report = evalgate.compare(tmp_path / "base", tmp_path / "cand", policy)
    assert report.status == "PASS", report.diagnostics
    assert SECRET in (tmp_path / "cand" / "cases.jsonl").read_text()  # stored on request
    report.to_json(tmp_path / "r.json")
    report.to_html(tmp_path / "r.html")
    report.to_markdown(tmp_path / "r.md")
    texts = [report.summary()] + [(tmp_path / f).read_text() for f in ("r.json", "r.html", "r.md")]
    for text in texts:
        assert SECRET not in text and SECRET_CASE not in text


def test_changed_evaluator_argument_is_invalid(tmp_path: Path) -> None:
    policy = write_pair(tmp_path, [short("1+aaa")], [short("1+bbb")])
    report = evalgate.compare(tmp_path / "base", tmp_path / "cand", policy)
    assert report.status == "INVALID"
    assert "evaluator_set_mismatch" in {d.code for d in report.diagnostics}


def test_native_against_hand_scored_is_invalid(tmp_path: Path) -> None:
    policy = write_pair(tmp_path, [], [short()])
    report = evalgate.compare(tmp_path / "base", tmp_path / "cand", policy)
    assert report.status == "INVALID"


def test_repeats_with_evaluators_round_trip_and_compare(tmp_path: Path) -> None:
    for name in ("base", "cand"):
        with new_run(tmp_path / name, run_id=name, repeats=2, evaluators=[polite]) as r:
            for i in range(2):
                for rep in range(2):
                    r.record(f"c{i}", repeat=rep, output="please", scores={"quality": 0.9})
    assert manifest(tmp_path / "base")["schema_version"] == "1.2"
    policy = tmp_path / "p.yaml"
    policy.write_text(
        json.dumps(
            {
                "version": 1,
                "comparison": {"min_compared_cases": 1},
                "metrics": {
                    "quality": {
                        "path": "scores.quality",
                        "direction": "maximize",
                        "max_mean_regression_abs": 0.02,
                    }
                },
                "checks": {"required": ["polite"]},
            }
        )
    )
    report = evalgate.compare(tmp_path / "base", tmp_path / "cand", policy)
    assert report.status == "PASS", report.diagnostics
