from pathlib import Path

import pytest
from conftest import POLICY, case, write_run

from evalgate.io import InputError, load_policy, load_run


def error_codes(exc: pytest.ExceptionInfo[InputError]) -> list[str]:
    return [d.code for d in exc.value.diagnostics]


def test_load_valid_run(tmp_path: Path) -> None:
    run = load_run(write_run(tmp_path / "r", [case("a"), case("b")]))
    assert [c.case_id for c in run.cases] == ["a", "b"]


def test_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        load_run(tmp_path / "nope")
    assert error_codes(exc) == ["run_dir_missing"]


def test_reports_every_bad_line(tmp_path: Path) -> None:
    d = write_run(tmp_path / "r", [case("a")])
    with (d / "cases.jsonl").open("a") as f:
        f.write("{not json\n")
        f.write('{"case_id": "c", "status": "ok", "scores": {"q": true}}\n')
        f.write('{"status": "ok"}\n')
    with pytest.raises(InputError) as exc:
        load_run(d)
    messages = [d.message for d in exc.value.diagnostics]
    for lineno in (2, 3, 4):
        assert any(f"cases.jsonl:{lineno}: " in m for m in messages), messages


def test_unsupported_schema_version_and_missing_cases(tmp_path: Path) -> None:
    d = write_run(tmp_path / "r", [case("a")], schema_version="2.0")
    (d / "cases.jsonl").unlink()
    with pytest.raises(InputError) as exc:
        load_run(d)
    assert error_codes(exc) == ["schema", "file_missing"]


def test_empty_cases_file(tmp_path: Path) -> None:
    d = write_run(tmp_path / "r", [])
    (d / "cases.jsonl").write_text("\n\n")
    with pytest.raises(InputError) as exc:
        load_run(d)
    assert error_codes(exc) == ["no_cases"]


def test_nan_rejected(tmp_path: Path) -> None:
    d = write_run(tmp_path / "r", [])
    (d / "cases.jsonl").write_text('{"case_id": "a", "status": "ok", "scores": {"q": NaN}}\n')
    with pytest.raises(InputError):
        load_run(d)


def test_policy_typo_rejected(tmp_path: Path) -> None:
    p = tmp_path / "evalgate.yaml"
    p.write_text(
        "version: 1\nmetrics:\n  q:\n    path: scores.q\n    direction: maximize\n"
        "    max_mean_regresion_abs: 0.1\n"
    )
    with pytest.raises(InputError) as exc:
        load_policy(p)
    assert "max_mean_regresion_abs" in exc.value.diagnostics[0].message


def test_policy_bad_yaml_and_bad_direction(tmp_path: Path) -> None:
    p = tmp_path / "evalgate.yaml"
    p.write_text("version: [1\n")
    with pytest.raises(InputError) as exc:
        load_policy(p)
    assert error_codes(exc) == ["policy_yaml"]
    p.write_text("version: 1\nmetrics:\n  q:\n    path: scores.q\n    direction: higher\n")
    with pytest.raises(InputError):
        load_policy(p)


def test_policy_fixture_is_valid(tmp_path: Path) -> None:
    import yaml

    p = tmp_path / "evalgate.yaml"
    p.write_text(yaml.safe_dump(POLICY))
    assert load_policy(p).checks.required == ["safety_valid"]
