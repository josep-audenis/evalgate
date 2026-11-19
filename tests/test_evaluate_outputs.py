"""evaluate_outputs (0.8.0 T2): OUTPUTS.jsonl + evaluators.yaml -> run, one engine with the SDK."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import evalgate
from evalgate import EvalRun, evaluate_outputs
from evalgate.evaluators import exact_match, json_valid, length
from evalgate.io import InputError, load_run

SECRET = "SENTINEL-4b2e-output"
MANIFEST = {"dataset_id": "d", "dataset_version": "v", "evaluator_version": "e", "run_id": "cand"}
EVALUATORS = [
    {"type": "json_valid", "name": "is_json"},
    {"type": "length", "name": "concise", "max": 40},
    {"type": "exact_match", "name": "correct", "normalize": ["strip"]},
]


def config(tmp_path: Path, **top: Any) -> Path:
    path = tmp_path / "evaluators.yaml"
    data = {"version": 1, "manifest": MANIFEST, "evaluators": EVALUATORS} | top
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def outputs(tmp_path: Path, lines: list[Any], raw: str = "") -> Path:
    path = tmp_path / "outputs.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines) + raw)
    return path


LINES: list[dict[str, Any]] = [
    {"case_id": "a", "status": "ok", "output": '{"x": 1}', "expected": '{"x": 1}'},
    {
        "case_id": "b",
        "status": "ok",
        "output": "not json",
        "expected": "nope",
        "metadata": {"category": "billing"},
        "metrics": {"latency_ms": 120},
    },
    {"case_id": "c", "status": "error", "error": "timeout", "metadata": {"category": "x"}},
]


def files(path: Path) -> tuple[bytes, bytes]:
    return (path / "run.json").read_bytes(), (path / "cases.jsonl").read_bytes()


def test_cli_engine_matches_the_sdk_byte_for_byte(tmp_path: Path) -> None:
    summary = evaluate_outputs(outputs(tmp_path, LINES), config(tmp_path), tmp_path / "cli")
    assert summary.cases == 3 and summary.repeats == 1 and summary.evaluator_errors == 0

    evs = [
        json_valid(name="is_json"),
        length(name="concise", max=40),
        exact_match(name="correct", normalize=["strip"]),
    ]
    with EvalRun(
        "cand",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=tmp_path / "sdk",
        evaluators=evs,
    ) as run:
        run.record(
            "a", output='{"x": 1}', case={"case_id": "a", "metadata": {}, "expected": '{"x": 1}'}
        )
        run.record(
            "b",
            output="not json",
            case={"case_id": "b", "metadata": {"category": "billing"}, "expected": "nope"},
            metrics={"latency_ms": 120},
            metadata={"category": "billing"},
        )
        run.record_error("c", "timeout", metadata={"category": "x"})
    assert files(tmp_path / "cli") == files(tmp_path / "sdk")


def test_results_and_case_fields(tmp_path: Path) -> None:
    evaluate_outputs(outputs(tmp_path, LINES), config(tmp_path), tmp_path / "out")
    a, b, c = load_run(tmp_path / "out").cases
    assert a.checks == {"is_json": True, "concise": True, "correct": True}
    assert b.checks == {"is_json": False, "concise": True, "correct": False}
    assert b.metrics == {"latency_ms": 120.0} and b.metadata == {"category": "billing"}
    assert (c.status, c.error) == ("error", "timeout")


def test_missing_expected_is_an_evaluator_error(tmp_path: Path) -> None:
    lines = [{"case_id": "a", "status": "ok", "output": "x"}]
    summary = evaluate_outputs(outputs(tmp_path, lines), config(tmp_path), tmp_path / "out")
    assert summary.evaluator_errors == 1
    (case,) = load_run(tmp_path / "out").cases
    assert (case.status, case.error) == ("error", "evaluator correct failed")


@pytest.mark.parametrize(
    "line,code",
    [
        ({"case_id": "a", "status": "ok"}, "outputs_record"),  # output missing
        ({"case_id": "a", "status": "ok", "output": "x", "error": "e"}, "outputs_record"),
        ({"case_id": "a", "status": "error", "output": "x"}, "outputs_record"),
        ({"case_id": "a", "status": "maybe", "output": "x"}, "outputs_record"),
        ({"case_id": "", "status": "ok", "output": "x"}, "outputs_record"),
        ({"case_id": "a", "status": "ok", "output": "x", "typo": 1}, "outputs_record"),
        ({"case_id": "a", "status": "ok", "output": "x", "metrics": {"m": "1"}}, "outputs_record"),
        ({"case_id": "a", "status": "ok", "output": "x", "repeat": -1}, "outputs_record"),
        (["not", "an", "object"], "outputs_parse"),
    ],
)
def test_bad_lines_fail_closed(tmp_path: Path, line: Any, code: str) -> None:
    with pytest.raises(InputError) as exc:
        evaluate_outputs(outputs(tmp_path, [LINES[0], line]), config(tmp_path), tmp_path / "out")
    assert {d.code for d in exc.value.diagnostics} == {code}
    assert "line 2" in exc.value.diagnostics[0].message
    assert not (tmp_path / "out" / "run.json").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "{not json\n",
        '{"case_id": "a", "case_id": "b", "status": "ok", "output": 1}\n',
        '{"case_id": "a", "status": "ok", "output": NaN}\n',
    ],
)
def test_unparseable_lines(tmp_path: Path, raw: str) -> None:
    with pytest.raises(InputError) as exc:
        evaluate_outputs(outputs(tmp_path, [], raw), config(tmp_path), tmp_path / "out")
    assert [d.code for d in exc.value.diagnostics] == ["outputs_parse"]


def test_every_bad_line_is_reported_without_output_text(tmp_path: Path) -> None:
    lines = [
        {"case_id": "a", "status": "error", "output": SECRET},
        {"case_id": "b", "status": SECRET, "output": SECRET},
        {"case_id": "c", "status": "ok", "output": SECRET, "metrics": {"m": SECRET}},
    ]
    raw = '{"case_id": "d", "output": "' + SECRET + '" oops}\n'
    with pytest.raises(InputError) as exc:
        evaluate_outputs(outputs(tmp_path, lines, raw), config(tmp_path), tmp_path / "out")
    messages = [d.message for d in exc.value.diagnostics]
    assert {m.split(": ")[1] for m in messages} == {"line 1", "line 2", "line 3", "line 4"}
    assert not any(SECRET in m for m in messages)


def test_empty_file(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        evaluate_outputs(outputs(tmp_path, [], "\n\n"), config(tmp_path), tmp_path / "out")
    assert [d.code for d in exc.value.diagnostics] == ["no_cases"]


def test_run_id_required_and_overridable(tmp_path: Path) -> None:
    cfg = config(tmp_path, manifest={k: v for k, v in MANIFEST.items() if k != "run_id"})
    src = outputs(tmp_path, LINES)
    with pytest.raises(InputError) as exc:
        evaluate_outputs(src, cfg, tmp_path / "out")
    assert [d.code for d in exc.value.diagnostics] == ["import_run_id"]
    evaluate_outputs(src, cfg, tmp_path / "out", run_id="given")
    assert load_run(tmp_path / "out").manifest.run_id == "given"


def test_existing_run_needs_overwrite(tmp_path: Path) -> None:
    src, cfg = outputs(tmp_path, LINES), config(tmp_path)
    evaluate_outputs(src, cfg, tmp_path / "out")
    with pytest.raises(InputError) as exc:
        evaluate_outputs(src, cfg, tmp_path / "out")
    assert [d.code for d in exc.value.diagnostics] == ["import_exists"]
    evaluate_outputs(src, cfg, tmp_path / "out", overwrite=True)


def test_store_output_from_config(tmp_path: Path) -> None:
    lines = [{"case_id": "a", "status": "ok", "output": SECRET, "expected": SECRET}]
    evaluate_outputs(outputs(tmp_path, lines), config(tmp_path), tmp_path / "plain")
    assert SECRET not in (tmp_path / "plain" / "cases.jsonl").read_text()
    evaluate_outputs(
        outputs(tmp_path, lines), config(tmp_path, store_output=True), tmp_path / "stored"
    )
    assert load_run(tmp_path / "stored").cases[0].output == SECRET


def test_repeats(tmp_path: Path) -> None:
    lines = [
        {"case_id": c, "status": "ok", "output": "x", "expected": "x", "repeat": r}
        for c in ("a", "b")
        for r in (0, 1)
    ]
    summary = evaluate_outputs(outputs(tmp_path, lines), config(tmp_path), tmp_path / "out")
    assert summary.repeats == 2
    run = load_run(tmp_path / "out")
    assert run.manifest.schema_version == "1.2" and run.manifest.repeats == 2


def test_config_errors_surface_before_reading_outputs(tmp_path: Path) -> None:
    bad = config(tmp_path, evaluators=[{"type": "matches_model", "name": "m"}])
    with pytest.raises(InputError) as exc:
        evaluate_outputs(tmp_path / "missing.jsonl", bad, tmp_path / "out")
    assert {d.code for d in exc.value.diagnostics} == {"schema"}


def test_exported_from_package() -> None:
    assert evalgate.evaluate_outputs is evaluate_outputs
