import json
from pathlib import Path

from conftest import case, write_run
from typer.testing import CliRunner

from evalgate import __version__
from evalgate.cli import EXIT_FAIL, EXIT_INVALID, EXIT_PASS, app

runner = CliRunner()
EXAMPLE = Path(__file__).parent.parent / "examples" / "quickstart"


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_validate_example() -> None:
    result = runner.invoke(app, ["validate", str(EXAMPLE / "candidate")])
    assert result.exit_code == EXIT_PASS, result.output


def test_validate_duplicate_ids(tmp_path: Path) -> None:
    run = write_run(tmp_path / "r", [case("a"), case("a")])
    result = runner.invoke(app, ["validate", str(run)])
    assert result.exit_code == EXIT_INVALID
    assert "duplicate_case_id" in result.output


def test_validate_missing_dir(tmp_path: Path) -> None:
    assert runner.invoke(app, ["validate", str(tmp_path / "x")]).exit_code == EXIT_INVALID


def test_compare_example_fails_on_safety_regression(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "compare",
            str(EXAMPLE / "baseline"),
            str(EXAMPLE / "candidate"),
            "--policy",
            str(EXAMPLE / "evalgate.yaml"),
            "--json",
            str(out),
        ],
    )
    assert result.exit_code == EXIT_FAIL, result.output
    assert "decision: FAIL" in result.output
    report = json.loads(out.read_text())
    assert report["status"] == "FAIL"
    assert report["baseline"]["run_id"] == "baseline-v1"
    assert {r["rule"] for r in report["rules"] if not r["passed"]} == {
        "checks.required.safety_valid",
        "checks.max_new_failures",
    }


def test_compare_identical_passes() -> None:
    base = str(EXAMPLE / "baseline")
    result = runner.invoke(app, ["compare", base, base, "--policy", str(EXAMPLE / "evalgate.yaml")])
    assert result.exit_code == EXIT_PASS, result.output


def test_compare_bad_input_is_invalid_with_json(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    result = runner.invoke(
        app,
        [
            "compare",
            str(tmp_path / "missing"),
            str(EXAMPLE / "candidate"),
            "--policy",
            str(tmp_path / "missing.yaml"),
            "--json",
            str(out),
        ],
    )
    assert result.exit_code == EXIT_INVALID
    report = json.loads(out.read_text())
    assert report["status"] == "INVALID"
    assert {d["code"] for d in report["diagnostics"]} == {"run_dir_missing", "file_missing"}


def test_internal_error_exits_invalid(monkeypatch: object, tmp_path: Path) -> None:
    import evalgate.cli as cli

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "compare_many", boom)  # type: ignore[attr-defined]
    base = str(EXAMPLE / "baseline")
    result = runner.invoke(app, ["compare", base, base, "--policy", str(EXAMPLE / "evalgate.yaml")])
    assert result.exit_code == EXIT_INVALID


def test_unencodable_output_does_not_turn_a_decision_into_exit_2(tmp_path: Path) -> None:
    # A non-ASCII run_id printed to a cp1252 pipe used to raise UnicodeEncodeError (exit 2).
    import os
    import shutil
    import subprocess
    import sys

    examples = Path(__file__).parent.parent / "examples" / "quickstart"
    cand = tmp_path / "cand"
    shutil.copytree(examples / "candidate", cand)
    manifest = json.loads((cand / "run.json").read_text(encoding="utf-8"))
    manifest["run_id"] = "日本✓"
    (cand / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    env = os.environ | {"PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(Path(evalgate_src()))}
    result = subprocess.run(
        [sys.executable, "-m", "evalgate.cli", "compare", str(examples / "baseline"), str(cand),
         "--policy", str(examples / "evalgate.yaml")],
        env=env, capture_output=True,
    )  # fmt: skip
    assert result.returncode == 1, result.stderr
    assert b"internal error" not in result.stdout + result.stderr


def evalgate_src() -> Path:
    import evalgate

    return Path(evalgate.__file__).parent.parent
