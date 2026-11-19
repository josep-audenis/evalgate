"""`evalgate calibrate` (0.9.0 T4): thin CLI over calibrate_run, reporting only."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from evalgate import EvalRun
from evalgate.cli import EXIT_INVALID, app
from evalgate.evaluators import check
from evalgate.models.calibration import CalibrationReport

runner = CliRunner()
SECRET = "SENTINEL-cal-0d9e-output"


@check(name="ok", version="1")
def ok(output: str, case: object) -> bool:
    return "yes" in output


def make_run(path: Path, cases: int = 4, repeats: int = 1) -> Path:
    with EvalRun(
        "judge-run",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=path,
        evaluators=[ok],
        store_output=True,
        repeats=repeats if repeats > 1 else None,
    ) as r:
        for i in range(cases):
            for k in range(repeats):
                output = f"{SECRET} {'yes' if i % 2 else 'no'}"
                r.record(f"c{i}", output=output, repeat=k if repeats > 1 else None)
    return path


def write_labels(path: Path, cases: int = 4) -> Path:
    lines = [json.dumps({"case_id": f"c{i}", "checks": {"ok": i % 2 == 1}}) for i in range(cases)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_report_exit_0_and_json(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run")
    labels = write_labels(tmp_path / "labels.jsonl")
    out = tmp_path / "cal.json"
    result = runner.invoke(
        app, ["calibrate", str(run_dir), "--labels", str(labels), "--json", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "judge-run" in result.output and "1.000" in result.output
    report = CalibrationReport.model_validate_json(out.read_text())
    assert report.entries[0].accuracy == 1.0 and report.entries[0].kappa == 1.0
    assert report.model_dump_json(indent=2) + "\n" == out.read_text()  # round-trips, stable
    assert SECRET not in result.output and SECRET not in out.read_text()
    assert "fewer than 30 labelled cases" in result.output


def test_warning_counts_cases_not_pooled_repeats(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run", cases=10, repeats=3)  # 30 rows, 10 cases
    labels = write_labels(tmp_path / "labels.jsonl", cases=10)
    result = runner.invoke(app, ["calibrate", str(run_dir), "--labels", str(labels)])
    assert result.exit_code == 0, result.output
    assert "fewer than 30 labelled cases" in result.output


def test_no_warning_with_enough_cases(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run", cases=30)
    labels = write_labels(tmp_path / "labels.jsonl", cases=30)
    result = runner.invoke(app, ["calibrate", str(run_dir), "--labels", str(labels)])
    assert result.exit_code == 0 and "warning" not in result.output


@pytest.mark.parametrize(
    ("labels", "code"),
    [
        ("not json\n", "labels_parse"),
        ('{"case_id": "c0"}\n', "labels_record"),
        ('{"case_id": "c0", "checks": {"typo": true}}\n', "labels_unknown"),
        ('{"case_id": "zz", "checks": {"ok": true}}\n', "labels_empty"),  # n = 0, not unknown
        ("", "labels_empty"),
    ],
)
def test_bad_input_exits_2(tmp_path: Path, labels: str, code: str) -> None:
    run_dir = make_run(tmp_path / "run")
    path = tmp_path / "labels.jsonl"
    path.write_text(labels, encoding="utf-8")
    out = tmp_path / "cal.json"
    result = runner.invoke(
        app, ["calibrate", str(run_dir), "--labels", str(path), "--json", str(out)]
    )
    assert result.exit_code == EXIT_INVALID
    assert code in result.output and "INVALID" in result.output
    assert not out.exists()


def test_invalid_run_exits_2(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run")
    (run_dir / "cases.jsonl").write_text("")
    out = tmp_path / "cal.json"
    labels = write_labels(tmp_path / "l.jsonl")
    result = runner.invoke(
        app, ["calibrate", str(run_dir), "--labels", str(labels), "--json", str(out)]
    )
    assert result.exit_code == EXIT_INVALID and "INVALID" in result.output
    assert not out.exists()


def test_missing_run_dir_exits_2(tmp_path: Path) -> None:
    labels = write_labels(tmp_path / "l.jsonl")
    result = runner.invoke(app, ["calibrate", str(tmp_path / "nope"), "--labels", str(labels)])
    assert result.exit_code == EXIT_INVALID


def test_help_lists_calibrate() -> None:
    result = runner.invoke(app, ["--help"])
    assert "calibrate" in result.output


def test_markup_in_names_is_printed_literally(tmp_path: Path) -> None:
    name = "x[/]\\"
    with EvalRun(
        "[bold red]inj[/]",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=tmp_path / "run",
    ) as r:
        r.record("c0", checks={name: True})
    labels = tmp_path / "l.jsonl"
    labels.write_text(json.dumps({"case_id": "c0", "checks": {name: True}}) + chr(10))
    result = runner.invoke(app, ["calibrate", str(tmp_path / "run"), "--labels", str(labels)])
    assert result.exit_code == 0, result.output
    assert "[bold red]inj[/]" in result.output and name in result.output
