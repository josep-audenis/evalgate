"""Generic importer: CSV / JSON / JSONL -> run directory, fail closed, no unmapped data."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

import evalgate
from evalgate import import_results
from evalgate.cli import EXIT_INVALID, EXIT_PASS, app
from evalgate.io import InputError, load_run

EXAMPLE = Path(__file__).parent.parent / "examples" / "import-csv"

MAPPING: dict[str, Any] = {
    "version": 1,
    "manifest": {
        "run_id": "r",
        "dataset_id": "d",
        "dataset_version": "v1",
        "evaluator_version": "e1",
    },
    "case_id": "id",
    "scores": {"quality": "q"},
    "checks": {"safe": "ok"},
    "metadata": {"category": "cat"},
}


def write(tmp: Path, name: str, content: str) -> Path:
    path = tmp / name
    path.write_text(content, encoding="utf-8")
    return path


def mapping(tmp: Path, **over: Any) -> Path:
    return write(tmp, "mapping.yaml", yaml.safe_dump(MAPPING | over))


def codes(exc: pytest.ExceptionInfo[InputError]) -> list[str]:
    return [d.code for d in exc.value.diagnostics]


def test_csv_import_round_trip_and_privacy(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat,prompt\na,0.5,yes,x,SECRET\nb,1,FALSE,y,SECRET\n")
    summary = import_results(src, mapping(tmp_path), tmp_path / "out")
    assert summary == evalgate.ImportSummary(cases=2, repeats=1, ignored_fields=["prompt"])
    run = load_run(tmp_path / "out")
    assert run.manifest.schema_version == "1.0" and run.manifest.run_id == "r"
    assert [(c.case_id, c.scores, c.checks, c.metadata) for c in run.cases] == [
        ("a", {"quality": 0.5}, {"safe": True}, {"category": "x"}),
        ("b", {"quality": 1.0}, {"safe": False}, {"category": "y"}),
    ]
    assert "SECRET" not in (tmp_path / "out" / "cases.jsonl").read_text()


def test_json_and_jsonl_with_dotted_paths(tmp_path: Path) -> None:
    rows = [{"id": "a", "res": {"q": 0.7, "ok": True}}, {"id": "b", "res": {"q": 0.9, "ok": 1}}]
    m = mapping(tmp_path, scores={"quality": "res.q"}, checks={"safe": "res.ok"}, metadata={})
    src = write(tmp_path, "r.json", json.dumps(rows))
    import_results(src, m, tmp_path / "j")
    src = write(tmp_path, "r.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
    import_results(src, m, tmp_path / "l")
    for out in ("j", "l"):
        cases = load_run(tmp_path / out).cases
        assert [(c.scores["quality"], c.checks["safe"]) for c in cases] == [
            (0.7, True),
            (0.9, True),
        ]


def test_every_bad_record_reported_and_nothing_written(tmp_path: Path) -> None:
    src = write(
        tmp_path,
        "r.csv",
        "id,q,ok,cat\na,0.5,yes,x\n,0.5,yes,x\nc,abc,yes,x\nd,0.5,maybe,x\ne,nan,yes,x\n",
    )
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path), tmp_path / "out")
    messages = [d.message for d in exc.value.diagnostics]
    assert [m.split("record ")[1].split(":")[0] for m in messages] == ["2", "3", "4", "5"]
    assert "case_id" in messages[0] and "number" in messages[1] and "true/false" in messages[2]
    assert not (tmp_path / "out" / "run.json").exists()


def test_status_mapping_and_error_records(tmp_path: Path) -> None:
    src = write(
        tmp_path, "r.csv", "id,q,ok,cat,st,msg\na,0.5,yes,x,success,\nb,,,x,timeout,too slow\n"
    )
    m = mapping(
        tmp_path, status="st", status_values={"success": "ok", "timeout": "error"}, error="msg"
    )
    import_results(src, m, tmp_path / "out")
    cases = load_run(tmp_path / "out").cases
    assert [(c.case_id, c.status, c.error) for c in cases] == [
        ("a", "ok", None),
        ("b", "error", "too slow"),
    ]


def test_unknown_status_value_is_an_error(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat,st\na,0.5,yes,x,skipped\n")
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path, status="st"), tmp_path / "out")
    assert "status_values" in exc.value.diagnostics[0].message


def test_blank_values_are_missing_not_zero(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,,yes,\n")
    import_results(src, mapping(tmp_path), tmp_path / "out")
    case = load_run(tmp_path / "out").cases[0]
    assert case.scores == {} and case.metadata == {}


def test_duplicate_case_ids_rejected(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,0.5,yes,x\na,0.6,yes,x\n")
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path), tmp_path / "out")
    assert "duplicate" in exc.value.diagnostics[0].message
    assert not (tmp_path / "out" / "run.json").exists()


def test_repeats_column(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,try,q,ok,cat\na,0,0.5,yes,x\na,1,0.7,yes,x\n")
    summary = import_results(src, mapping(tmp_path, repeat="try"), tmp_path / "out")
    assert summary.repeats == 2
    run = load_run(tmp_path / "out")
    assert run.manifest.schema_version == "1.1" and run.manifest.repeats == 2
    with pytest.raises(InputError):
        bad = write(tmp_path, "b.csv", "id,try,q,ok,cat\na,-1,0.5,yes,x\n")
        import_results(bad, mapping(tmp_path, repeat="try"), tmp_path / "bad")


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"scores": {}, "checks": {}}, "at least one"),
        ({"status_values": {"x": "ok"}}, "status_values needs"),
        ({"case_idd": "id"}, "case_idd"),
        ({"version": 2}, "version"),
    ],
)
def test_bad_mapping_rejected(tmp_path: Path, over: dict[str, Any], message: str) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,0.5,yes,x\n")
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path, **over), tmp_path / "out")
    assert message in exc.value.diagnostics[0].message


def test_run_id_required_and_overridable(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,0.5,yes,x\n")
    m = mapping(tmp_path, manifest=MAPPING["manifest"] | {"run_id": None})
    with pytest.raises(InputError) as exc:
        import_results(src, m, tmp_path / "out")
    assert codes(exc) == ["import_run_id"]
    import_results(src, m, tmp_path / "out", run_id="given")
    assert load_run(tmp_path / "out").manifest.run_id == "given"


def test_overwrite_protection(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,0.5,yes,x\n")
    import_results(src, mapping(tmp_path), tmp_path / "out")
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path), tmp_path / "out")
    assert codes(exc) == ["import_exists"]
    import_results(src, mapping(tmp_path), tmp_path / "out", overwrite=True)


def test_unknown_format_and_unparseable_json(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        import_results(write(tmp_path, "r.txt", "x"), mapping(tmp_path), tmp_path / "o")
    assert codes(exc) == ["import_format"]
    with pytest.raises(InputError) as exc:
        import_results(write(tmp_path, "r.json", "{not json"), mapping(tmp_path), tmp_path / "o")
    assert codes(exc) == ["import_parse"]
    with pytest.raises(InputError) as exc:
        import_results(write(tmp_path, "r.json", '{"a": 1}'), mapping(tmp_path), tmp_path / "o")
    assert codes(exc) == ["import_parse"]


def test_cli_example_end_to_end(tmp_path: Path) -> None:
    runner = CliRunner()
    for side in ("baseline", "candidate"):
        result = runner.invoke(
            app,
            [
                "import",
                str(EXAMPLE / f"{side}.csv"),
                "--mapping",
                str(EXAMPLE / "mapping.yaml"),
                "--out",
                str(tmp_path / side),
                "--run-id",
                f"faq-bot-{side}",
            ],
        )
        assert result.exit_code == EXIT_PASS, result.output
        assert "not imported (unmapped): prompt, response" in result.output
    report = evalgate.compare(
        tmp_path / "baseline", tmp_path / "candidate", EXAMPLE / "evalgate.yaml"
    )
    assert report.status == "FAIL"
    assert {r.rule for r in report.rules if not r.passed} == {
        "max_execution_errors",
        "checks.required.cites_source",
    }


def test_cli_invalid_input_exits_2(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,abc,yes,x\n")
    result = CliRunner().invoke(
        app, ["import", str(src), "--mapping", str(mapping(tmp_path)), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == EXIT_INVALID
    assert "nothing was written" in result.output
