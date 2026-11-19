"""`evalgate evaluate` (0.8.0 T3): thin CLI over evaluate_outputs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from evalgate import __version__, evaluate_outputs
from evalgate.cli import EXIT_INVALID, app
from evalgate.io import load_run

runner = CliRunner()
MANIFEST = {"dataset_id": "d", "dataset_version": "v", "evaluator_version": "e", "run_id": "cand"}


def setup(tmp_path: Path, lines: list[dict[str, object]]) -> tuple[Path, Path]:
    cfg = tmp_path / "evaluators.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "manifest": MANIFEST,
                "evaluators": [
                    {"type": "json_valid", "name": "is_json"},
                    {"type": "exact_match", "name": "correct"},
                ],
            }
        )
    )
    src = tmp_path / "outputs.jsonl"
    src.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return src, cfg


GOOD = [
    {"case_id": "a", "status": "ok", "output": "{}", "expected": "{}"},
    {"case_id": "b", "status": "ok", "output": "x", "expected": "x"},
]


def test_success_writes_the_same_files_as_the_sdk(tmp_path: Path) -> None:
    src, cfg = setup(tmp_path, GOOD)
    result = runner.invoke(
        app, ["evaluate", str(src), "--config", str(cfg), "--out", str(tmp_path / "cli")]
    )
    assert result.exit_code == 0, result.output
    assert "evaluated: 2 cases" in result.output
    evaluate_outputs(src, cfg, tmp_path / "sdk")
    for name in ("run.json", "cases.jsonl"):
        assert (tmp_path / "cli" / name).read_bytes() == (tmp_path / "sdk" / name).read_bytes()


def test_evaluator_errors_are_reported_but_still_exit_0(tmp_path: Path) -> None:
    src, cfg = setup(tmp_path, [{"case_id": "a", "status": "ok", "output": "x"}])  # no expected
    result = runner.invoke(
        app, ["evaluate", str(src), "--config", str(cfg), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == 0, result.output
    assert "1 evaluator error(s)" in result.output


def test_bad_input_exits_2_and_writes_nothing(tmp_path: Path) -> None:
    src, cfg = setup(tmp_path, [{"case_id": "a", "status": "ok"}])
    result = runner.invoke(
        app, ["evaluate", str(src), "--config", str(cfg), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == EXIT_INVALID
    assert "outputs_record" in result.output and "nothing was written" in result.output
    assert not (tmp_path / "out").exists()


def test_bad_config_exits_2(tmp_path: Path) -> None:
    src, _ = setup(tmp_path, GOOD)
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\n")
    result = runner.invoke(
        app, ["evaluate", str(src), "--config", str(bad), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == EXIT_INVALID


def test_run_id_override_and_overwrite(tmp_path: Path) -> None:
    src, cfg = setup(tmp_path, GOOD)
    args = ["evaluate", str(src), "--config", str(cfg), "--out", str(tmp_path / "out")]
    assert runner.invoke(app, [*args, "--run-id", "given"]).exit_code == 0
    assert load_run(tmp_path / "out").manifest.run_id == "given"
    again = runner.invoke(app, args)
    assert again.exit_code == EXIT_INVALID and "import_exists" in again.output
    assert runner.invoke(app, [*args, "--overwrite"]).exit_code == 0
    assert load_run(tmp_path / "out").manifest.run_id == "cand"


def test_help_lists_the_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert "evaluate" in result.output
    assert "--config" in runner.invoke(app, ["evaluate", "--help"]).output


def test_python_dash_m_runs_the_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "evalgate.cli", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0 and result.stdout.strip() == f"evalgate {__version__}"


def test_unexpected_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("evalgate.cli.evaluate_outputs", boom)
    src, cfg = setup(tmp_path, GOOD)
    result = runner.invoke(
        app, ["evaluate", str(src), "--config", str(cfg), "--out", str(tmp_path / "out")]
    )
    assert result.exit_code == EXIT_INVALID and "internal error" in result.output


def test_out_path_markup_is_not_interpreted(tmp_path: Path) -> None:
    src, cfg = setup(tmp_path, GOOD)
    out = tmp_path / "[red]out"
    result = runner.invoke(app, ["evaluate", str(src), "--config", str(cfg), "--out", str(out)])
    assert result.exit_code == 0 and "[red]out" in result.output
