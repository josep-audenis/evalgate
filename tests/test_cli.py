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

    def boom(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_compare", boom)  # type: ignore[attr-defined]
    base = str(EXAMPLE / "baseline")
    result = runner.invoke(app, ["compare", base, base, "--policy", str(EXAMPLE / "evalgate.yaml")])
    assert result.exit_code == EXIT_INVALID
