"""Schema 1.2 (native-evaluators design §4): `evaluators` manifest field, opaque `output`."""

import json
import math
from pathlib import Path

import pytest
from conftest import case, make_policy, make_run, write_run

from evalgate import EvalRun
from evalgate.engine import evaluate
from evalgate.io import InputError, load_run
from evalgate.models import Report, RunManifest

V12 = {"schema_version": "1.2"}
EVALS = {"evaluators": {"is_json": "1+abc", "short": "1+def"}}


def errors(report: Report) -> set[str]:
    return {d.code for d in report.diagnostics if d.level == "error"}


def test_run_without_evaluators_writes_same_bytes_as_0_6(tmp_path: Path) -> None:
    with EvalRun("r", dataset="d", dataset_version="v", evaluator_version="e", path=tmp_path) as r:
        r.record("a", scores={"quality": 1.0})
    # Golden 0.6.0 output: no `evaluators`, no `output`.
    expected_manifest = {
        "schema_version": "1.0",
        "run_id": "r",
        "dataset_id": "d",
        "dataset_version": "v",
        "evaluator_version": "e",
        "application": None,
        "application_version": None,
    }
    run_json = json.dumps(expected_manifest, indent=2)
    assert (tmp_path / "run.json").read_text().splitlines() == run_json.splitlines()
    expected_case = '{"case_id":"a","status":"ok","scores":{"quality":1.0}}'
    assert (tmp_path / "cases.jsonl").read_text().splitlines() == [expected_case]


def test_manifest_dump_omits_unset_evaluators() -> None:
    m = make_run([case("a")]).manifest
    assert "evaluators" not in m.model_dump()
    assert make_run([case("a")], **V12, **EVALS).manifest.model_dump()["evaluators"] == {
        "is_json": "1+abc",
        "short": "1+def",
    }


def test_1_2_with_evaluators_compares() -> None:
    run = make_run([case("a")], **V12, **EVALS)
    report = evaluate(run, run, make_policy())
    assert report.status == "PASS", report.diagnostics


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_pre_1_2_run_declaring_evaluators_is_invalid(version: str) -> None:
    run = make_run([case("a")], schema_version=version, **EVALS)
    assert errors(evaluate(run, run, make_policy())) == {"evaluators_schema"}


@pytest.mark.parametrize("bad", ["x", [], {}, {"n": ""}, {"": "1"}, {"n": 1}])
def test_malformed_evaluators_field_is_rejected_on_load(tmp_path: Path, bad: object) -> None:
    write_run(tmp_path / "r", [case("a")], **V12, evaluators=bad)
    with pytest.raises(InputError):
        load_run(tmp_path / "r")


def test_repeats_accept_schema_1_2() -> None:
    cases = [case("a", repeat=0), case("a", repeat=1)]
    run = make_run(cases, **V12, **EVALS)
    assert evaluate(run, run, make_policy()).status == "PASS"


def test_unknown_schema_version_still_rejected() -> None:
    with pytest.raises(ValueError):
        RunManifest.model_validate(
            {
                "schema_version": "1.3",
                "run_id": "r",
                "dataset_id": "d",
                "dataset_version": "v",
                "evaluator_version": "e",
            }
        )


def test_output_is_opaque_on_any_schema(tmp_path: Path) -> None:
    # A 0.6 run that used `output` as a custom field (even NaN) loads and decides unchanged.
    plain = write_run(tmp_path / "plain", [case("a"), case("b", quality=0.5)])
    cases = [case("a", output={"text": "hi"}), case("b", quality=0.5)]
    with_output = tmp_path / "out"
    write_run(with_output, cases)
    (with_output / "cases.jsonl").write_text(
        (with_output / "cases.jsonl")
        .read_text()
        .replace('"case_id": "b"', '"output": NaN, "case_id": "b"')
    )
    base = load_run(plain)
    cand = load_run(with_output)
    assert cand.cases[0].output == {"text": "hi"}
    assert isinstance(cand.cases[1].output, float) and math.isnan(cand.cases[1].output)
    policy = make_policy()
    expected = evaluate(base, load_run(plain), policy)
    got = evaluate(base, cand, policy)
    assert got.status == expected.status
    assert got.model_dump(exclude={"candidate"}) == expected.model_dump(exclude={"candidate"})
