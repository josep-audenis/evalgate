import json
from pathlib import Path
from typing import Any

import pytest
from conftest import POLICY
from pydantic import ValidationError
from typer.testing import CliRunner

import evalgate
from evalgate import EvalRun
from evalgate.cli import app
from evalgate.io import InputError, load_run

IDENTITY: dict[str, Any] = {
    "dataset": "suite",
    "dataset_version": "v1",
    "evaluator_version": "e1",
}


def record_run(path: Path, quality: float, safe: bool = True, **kw: Any) -> Path:
    with EvalRun(path.name, path=path, **IDENTITY, **kw) as run:
        for i in range(3):
            run.record(
                f"case-{i}",
                scores={"quality": quality},
                checks={"safety_valid": safe},
                metrics={"latency_ms": 1000},
                metadata={"category": "x"},
            )
        run.record_error("case-3", "timeout")
    return path


def test_recorded_run_round_trips(tmp_path: Path) -> None:
    run = load_run(record_run(tmp_path / "base", 0.9, application="bot", git_sha="abc"))
    assert run.manifest.run_id == "base"
    assert run.manifest.model_extra == {"git_sha": "abc"}
    assert [c.status for c in run.cases] == ["ok", "ok", "ok", "error"]
    assert run.cases[3].error == "timeout"
    assert not (tmp_path / "base" / ".cases.jsonl.partial").exists()


def test_interrupted_run_is_not_loadable(tmp_path: Path) -> None:
    path = tmp_path / "run"
    with pytest.raises(RuntimeError), EvalRun("run", path=path, **IDENTITY) as run:
        run.record("a", scores={"quality": 1.0})
        raise RuntimeError("pipeline crashed")
    with pytest.raises(InputError) as exc:
        load_run(path)
    assert {d.code for d in exc.value.diagnostics} == {"file_missing"}


def test_existing_run_protected_and_overwrite_clears_marker(tmp_path: Path) -> None:
    path = record_run(tmp_path / "run", 0.9)
    with pytest.raises(FileExistsError):
        EvalRun("run", path=path, **IDENTITY).__enter__()
    # A crashed overwrite must not leave the previous run looking complete.
    with pytest.raises(RuntimeError), EvalRun("run", path=path, overwrite=True, **IDENTITY):
        raise RuntimeError
    with pytest.raises(InputError):
        load_run(path)


def test_record_validates_immediately(tmp_path: Path) -> None:
    with EvalRun("run", path=tmp_path / "run", **IDENTITY) as run:
        run.record("a", scores={"quality": 0.5})
        with pytest.raises(ValueError, match="duplicate"):
            run.record("a")
        with pytest.raises(ValidationError):
            run.record("b", scores={"quality": float("nan")})
    with pytest.raises(RuntimeError):
        run.record("c")


def test_sdk_and_cli_reports_are_identical(tmp_path: Path) -> None:
    import yaml

    base = record_run(tmp_path / "base", 0.9)
    cand = record_run(tmp_path / "cand", 0.95, safe=False)
    policy = tmp_path / "evalgate.yaml"
    policy.write_text(yaml.safe_dump(POLICY | {"max_execution_errors": 1}))

    report = evalgate.compare(base, cand, policy)
    assert report.status == "FAIL"
    report.to_json(tmp_path / "sdk.json")
    result = CliRunner().invoke(
        app,
        [
            "compare",
            str(base),
            str(cand),
            "--policy",
            str(policy),
            "--json",
            str(tmp_path / "cli.json"),
        ],
    )
    assert result.exit_code == 1
    assert (tmp_path / "sdk.json").read_text() == (tmp_path / "cli.json").read_text()
    assert "decision: FAIL" in report.summary()


def test_sdk_compare_bad_input_is_invalid(tmp_path: Path) -> None:
    report = evalgate.compare(tmp_path / "a", tmp_path / "b", tmp_path / "p.yaml")
    assert report.status == "INVALID"
    assert json.loads(report.model_dump_json())["comparison"] is None
