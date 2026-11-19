"""History store: append, ids, strict reading, atomicity, privacy (phase-7 §2, 0.10.0 T2)."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import evalgate
from evalgate import EvalRun, read_history, record_report, record_run
from evalgate.io import InputError
from evalgate.io.history import load_entry_report, report_path
from evalgate.models.history import CompareEntry, RunEntry

EXAMPLES = Path(__file__).parent.parent / "examples"
QS = EXAMPLES / "quickstart"
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
SECRET = "SENTINEL-store-91c2"


def fail_report() -> evalgate.Report:
    return evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml")


def codes(exc: pytest.ExceptionInfo[InputError]) -> list[str]:
    return [d.code for d in exc.value.diagnostics]


def index_lines(store: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (store / "index.jsonl").read_text().splitlines()]


# --- append ----------------------------------------------------------------------------------


def test_ids_are_sequential_and_store_is_created(tmp_path: Path) -> None:
    store = tmp_path / "a" / "b"
    first = record_report(fail_report(), store, recorded_at=T0, label="v2")
    second = record_run(QS / "candidate", store, recorded_at=T0)
    assert (first.id, second.id) == (1, 2)
    assert isinstance(first, CompareEntry) and isinstance(second, RunEntry)
    assert [e.id for e in read_history(store)] == [1, 2]
    assert index_lines(store)[0]["label"] == "v2"
    assert (store / "index.jsonl").read_bytes().count(b"\r") == 0


def test_stored_report_is_byte_identical_to_json(tmp_path: Path) -> None:
    report = fail_report()
    entry = record_report(report, tmp_path, recorded_at=T0)
    report.to_json(tmp_path / "out.json")
    stored = report_path(tmp_path, 1).read_bytes()
    assert stored == (tmp_path / "out.json").read_bytes()
    assert entry.report_sha256 == hashlib.sha256(stored).hexdigest()
    loaded_entry, loaded = load_entry_report(tmp_path, 1)
    assert loaded == report and loaded_entry == entry


def test_invalid_compare_is_recorded(tmp_path: Path) -> None:
    report = evalgate.compare(tmp_path / "x", tmp_path / "y", tmp_path / "p.yaml")
    entry = record_report(report, tmp_path / "store", recorded_at=T0)
    assert (entry.status, entry.baseline, entry.metrics) == ("INVALID", None, [])


def test_recorded_at_makes_the_store_reproducible(tmp_path: Path) -> None:
    for store in ("a", "b"):
        record_report(fail_report(), tmp_path / store, recorded_at=T0)
        record_run(QS / "baseline", tmp_path / store, recorded_at=T0)
    assert (tmp_path / "a" / "index.jsonl").read_bytes() == (
        tmp_path / "b" / "index.jsonl"
    ).read_bytes()
    assert index_lines(tmp_path / "a")[0]["recorded_at"] == "2026-11-19T12:00:00Z"


def test_naive_recorded_at_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        record_report(fail_report(), tmp_path, recorded_at=datetime(2026, 1, 1))


def test_default_recorded_at_is_now(tmp_path: Path) -> None:
    entry = record_report(fail_report(), tmp_path)
    assert entry.recorded_at.startswith(str(datetime.now(UTC).year))


def test_long_label_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        record_report(fail_report(), tmp_path, label="x" * 201)
    assert codes(exc) == ["history_record"]
    assert not (tmp_path / "index.jsonl").exists()


# --- ids after pruning -----------------------------------------------------------------------


@pytest.mark.parametrize("drop", [1, 2], ids=["middle", "tail"])
def test_next_id_after_pruning(tmp_path: Path, drop: int) -> None:
    for _ in range(3):
        record_run(QS / "baseline", tmp_path, recorded_at=T0)
    lines = (tmp_path / "index.jsonl").read_text().splitlines()
    del lines[drop]
    (tmp_path / "index.jsonl").write_text("\n".join(lines) + "\n")
    entry = record_run(QS / "baseline", tmp_path, recorded_at=T0)
    assert entry.id == (4 if drop == 1 else 3)  # last id + 1


# --- strict reading --------------------------------------------------------------------------


def store_with(tmp_path: Path, *extra: str) -> Path:
    record_run(QS / "baseline", tmp_path, recorded_at=T0)
    record_run(QS / "baseline", tmp_path, recorded_at=T0)
    text = (tmp_path / "index.jsonl").read_text()
    (tmp_path / "index.jsonl").write_text(text + "".join(extra))
    return tmp_path


def entry_line(tmp_path: Path, **change: Any) -> str:
    line = index_lines(tmp_path)[0] | change
    return json.dumps(line) + "\n"


@pytest.mark.parametrize(
    "bad",
    [
        lambda p: entry_line(p, id=2),  # duplicate
        lambda p: entry_line(p, id=1),  # decreasing
        lambda p: entry_line(p, id=3, history_version=2),
        lambda p: entry_line(p, id=3, surprise=SECRET),
        lambda p: '{"history_version": 1, "id": 3, "kind": "run", "trunc',
        lambda p: f'{{"id": 3, "label": "{SECRET}", "label": "x"}}\n',
        lambda p: "[1, 2]\n",
    ],
)
def test_bad_lines_are_history_parse_and_block_appends(tmp_path: Path, bad: Any) -> None:
    store_with(tmp_path)
    (tmp_path / "index.jsonl").write_text((tmp_path / "index.jsonl").read_text() + bad(tmp_path))
    before = (tmp_path / "index.jsonl").read_bytes()
    for action in (lambda: read_history(tmp_path), lambda: record_run(QS / "baseline", tmp_path)):
        with pytest.raises(InputError) as exc:
            action()
        assert set(codes(exc)) == {"history_parse"}
        assert SECRET not in str(exc.value)
        assert "line 3" in str(exc.value)
    assert (tmp_path / "index.jsonl").read_bytes() == before


def test_crlf_and_missing_final_newline_are_read_and_normalised(tmp_path: Path) -> None:
    store_with(tmp_path)
    text = (tmp_path / "index.jsonl").read_text()
    (tmp_path / "index.jsonl").write_bytes(text.rstrip("\n").replace("\n", "\r\n").encode())
    assert [e.id for e in read_history(tmp_path)] == [1, 2]
    record_run(QS / "baseline", tmp_path, recorded_at=T0)
    raw = (tmp_path / "index.jsonl").read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n") and raw.count(b"\n") == 3


def test_missing_store(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        read_history(tmp_path)
    assert codes(exc) == ["history_missing"]


def test_store_path_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    with pytest.raises(InputError) as exc:
        record_report(fail_report(), tmp_path / "f")
    assert codes(exc) == ["history_write"]


# --- reports ---------------------------------------------------------------------------------


def test_unknown_id_run_entry_and_tampered_report(tmp_path: Path) -> None:
    record_report(fail_report(), tmp_path, recorded_at=T0)
    record_run(QS / "baseline", tmp_path, recorded_at=T0)
    for entry_id in (9, 2):
        with pytest.raises(InputError) as exc:
            load_entry_report(tmp_path, entry_id)
        assert codes(exc) == ["history_id"]
    path = report_path(tmp_path, 1)
    path.write_text(path.read_text().replace('"FAIL"', '"PASS"'))
    with pytest.raises(InputError) as exc:
        load_entry_report(tmp_path, 1)
    assert codes(exc) == ["history_id"]
    path.unlink()
    with pytest.raises(InputError) as exc:
        load_entry_report(tmp_path, 1)
    assert codes(exc) == ["history_id"]


# --- runs ------------------------------------------------------------------------------------


def test_invalid_run_is_not_recorded(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    with EvalRun("r", dataset="d", dataset_version="v", evaluator_version="e", path=run_dir) as r:
        r.record("a", scores={"q": 1})
    manifest = json.loads((run_dir / "run.json").read_text())
    (run_dir / "run.json").write_text(
        json.dumps(manifest | {"repeats": 3, "schema_version": "1.1"})
    )
    with pytest.raises(InputError) as exc:
        record_run(run_dir, tmp_path / "store")
    assert "history_record" in codes(exc)
    assert not (tmp_path / "store").exists()


def test_unloadable_run_reports_reader_codes(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        record_run(tmp_path / "nope", tmp_path / "store")
    assert codes(exc) == ["run_dir_missing"]


def test_overflowing_mean_is_history_record(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    with EvalRun("r", dataset="d", dataset_version="v", evaluator_version="e", path=run_dir) as r:
        r.record("a", scores={"q": 1e308})
        r.record("b", scores={"q": 1e308})
    with pytest.raises(InputError) as exc:
        record_run(run_dir, tmp_path / "store")
    assert codes(exc) == ["history_record"]


def test_no_outputs_and_no_temp_files_in_the_store(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    with EvalRun(
        "r",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=run_dir,
        store_output=True,
    ) as r:
        r.record("a", output=SECRET, scores={"q": 1}, checks={"ok": False})
    store = tmp_path / "store"
    record_run(run_dir, store, recorded_at=T0)
    record_report(fail_report(), store, recorded_at=T0)
    files = [p for p in store.rglob("*") if p.is_file()]
    assert sorted(p.name for p in files) == [
        "000002.json",
        "index.jsonl",
    ]  # run entry 1 has no report
    assert all(SECRET not in p.read_text() for p in files)
