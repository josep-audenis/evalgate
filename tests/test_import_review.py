"""Regression tests for review findings on the importer (PR #13). One test per finding."""

import json
from pathlib import Path
from typing import Any

import pytest
from test_import import MAPPING, codes, mapping, write

from evalgate import import_results
from evalgate.io import InputError, load_run
from evalgate.io.importer import load_mapping


def test_ragged_csv_rows_rejected_and_nothing_written(tmp_path: Path) -> None:
    # An unquoted "1,200" shifts columns: 200 must never be imported as the score.
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,0.5,yes,x\nb,1,200,yes,x\nc\n")
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path), tmp_path / "out")
    messages = [d.message for d in exc.value.diagnostics]
    assert len(messages) == 2
    assert "record 2" in messages[0] and "unquoted comma" in messages[0]
    assert "record 3" in messages[1]
    assert not (tmp_path / "out" / "cases.jsonl").exists()


def test_duplicate_csv_headers_rejected(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,q,ok,cat\na,0.1,0.9,yes,x\n")
    with pytest.raises(InputError, match="duplicate column"):
        import_results(src, mapping(tmp_path), tmp_path / "o")


def test_duplicate_json_keys_rejected(tmp_path: Path) -> None:
    src = write(tmp_path, "r.json", '[{"id": "a", "q": 0.1, "q": 0.9, "ok": true, "cat": "x"}]')
    with pytest.raises(InputError, match="duplicate key"):
        import_results(src, mapping(tmp_path), tmp_path / "o")


def test_mapped_field_absent_everywhere_is_an_error(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat\na,0.5,yes,x\n")
    with pytest.raises(InputError) as exc:
        import_results(src, mapping(tmp_path, metrics={"lat": "latncy"}), tmp_path / "o")
    assert codes(exc) == ["import_field_missing"]
    assert "latncy" in exc.value.diagnostics[0].message


@pytest.mark.parametrize("bad_id", [{"x": 1}, [1], True, 1.5])
def test_non_scalar_case_id_rejected(tmp_path: Path, bad_id: Any) -> None:
    rows = [{"id": bad_id, "q": 0.5, "ok": True, "cat": "x"}]
    src = write(tmp_path, "r.json", json.dumps(rows))
    with pytest.raises(InputError, match="text or an integer"):
        import_results(src, mapping(tmp_path), tmp_path / "o")


@pytest.mark.parametrize("value", ["1_000", "١٢", "１", "1,200", "0x10", "None"])
def test_number_text_must_be_plain_ascii(tmp_path: Path, value: str) -> None:
    rows = [{"id": "a", "q": value, "ok": True, "cat": "x"}]
    src = write(tmp_path, "r.json", json.dumps(rows))
    with pytest.raises(InputError, match="number"):
        import_results(src, mapping(tmp_path), tmp_path / "o")


def test_huge_json_integer_rejected_not_a_crash(tmp_path: Path) -> None:
    huge = "1" + "0" * 400
    src = write(tmp_path, "r.json", f'[{{"id": "a", "q": {huge}, "ok": true, "cat": "x"}}]')
    with pytest.raises(InputError, match="finite number"):
        import_results(src, mapping(tmp_path), tmp_path / "o")


def test_numeric_text_int_ids_and_str_paths_accepted(tmp_path: Path) -> None:
    rows = [{"id": 7, "q": " 9e-1 ", "ok": "PASS", "cat": "x"}]
    src = write(tmp_path, "r.json", json.dumps(rows))
    import_results(str(src), str(mapping(tmp_path)), str(tmp_path / "o"))  # plain str paths
    case = load_run(tmp_path / "o").cases[0]
    assert (case.case_id, case.scores, case.checks) == ("7", {"quality": 0.9}, {"safe": True})


def test_status_values_keys_are_trimmed(tmp_path: Path) -> None:
    src = write(tmp_path, "r.csv", "id,q,ok,cat,st\na,0.5,yes,x,done\n")
    m = mapping(tmp_path, status="st", status_values={" done ": "ok"})
    import_results(src, m, tmp_path / "o")
    assert load_run(tmp_path / "o").cases[0].status == "ok"


def test_failed_overwrite_keeps_old_run_and_leaves_no_partial(tmp_path: Path) -> None:
    good = write(tmp_path, "g.csv", "id,q,ok,cat\na,0.5,yes,x\n")
    import_results(good, mapping(tmp_path), tmp_path / "o")
    before = (tmp_path / "o" / "cases.jsonl").read_bytes()
    dup = write(tmp_path, "d.csv", "id,q,ok,cat\nb,0.5,yes,x\nb,0.6,yes,x\n")
    with pytest.raises(InputError):
        import_results(dup, mapping(tmp_path), tmp_path / "o", overwrite=True)
    assert (tmp_path / "o" / "cases.jsonl").read_bytes() == before
    assert not (tmp_path / "o" / ".cases.jsonl.partial").exists()


def test_documented_mapping_example_is_valid(tmp_path: Path) -> None:
    doc = (Path(__file__).parent.parent / "docs" / "importing.md").read_text(encoding="utf-8")
    block = doc.split("## Mapping file", 1)[1].split("```yaml\n", 1)[1].split("```", 1)[0]
    m = load_mapping(write(tmp_path, "m.yaml", block))
    assert m.manifest.application_version == "1.4" and m.repeat == "attempt"
    assert MAPPING["version"] == m.version
