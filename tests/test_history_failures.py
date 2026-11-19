"""History v2, case_ids and `evalgate history failures` (phase-7 §4, 0.12.0 T3)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import evalgate
from evalgate import EvalRun, read_history, record_report, record_run
from evalgate.cli import EXIT_INVALID, app
from evalgate.comparison.history import recurring_failures
from evalgate.io import InputError
from evalgate.io.history import load_entry_report

runner = CliRunner()
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
QS = Path(__file__).parent.parent / "examples" / "quickstart"


def make_run(
    path: Path,
    checks: dict[str, dict[str, bool]],
    *,
    errored: tuple[str, ...] = (),
    dataset: str = "d",
) -> Path:
    with EvalRun(
        path.name, dataset=dataset, dataset_version="v", evaluator_version="e", path=path
    ) as r:
        for case_id, case_checks in checks.items():
            if case_id in errored:
                r.record_error(case_id, "timeout")
            else:
                r.record(case_id, checks=case_checks, scores={"q": 1.0})
    return path


def store_of(
    tmp_path: Path,
    *runs: dict[str, dict[str, bool]],
    errored: dict[int, tuple[str, ...]] | None = None,
) -> Path:
    store = tmp_path / "h"
    for i, checks in enumerate(runs):
        run_dir = make_run(tmp_path / f"r{i}", checks, errored=(errored or {}).get(i, ()))
        record_run(run_dir, store, recorded_at=T0)
    return store


def pairs(store: Path, **kwargs: Any) -> dict[tuple[str, str], dict[str, Any]]:
    result = recurring_failures(read_history(store), **kwargs)
    return {(p["case_id"], p["check"]): p for p in result["pairs"]}


# --- v2 entries ------------------------------------------------------------------------------


def test_new_run_entries_are_v2_with_case_ids(tmp_path: Path) -> None:
    store = store_of(
        tmp_path, {"a": {"s": True}, "b": {"s": False}, "c": {"s": True}}, errored={0: ("c",)}
    )
    [entry] = read_history(store)
    assert entry.history_version == 2
    assert entry.case_ids == ["a", "b"]  # type: ignore[union-attr]  # ok cases only


def test_mixed_v1_and_v2_store_reads(tmp_path: Path) -> None:
    store = store_of(tmp_path, {"a": {"s": False}}, {"a": {"s": False}})
    lines = (store / "index.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["history_version"] = 1
    del first["case_ids"]
    lines[0] = json.dumps(first)
    (store / "index.jsonl").write_text("\n".join(lines) + "\n")
    entries = read_history(store)
    assert [e.history_version for e in entries] == [1, 2]
    assert pairs(store)[("a", "s")]["of"] is None  # a v1 entry has no case_ids


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e | {"history_version": 1},  # case_ids on a v1 line
        lambda e: {k: v for k, v in e.items() if k != "case_ids"},  # missing on a v2 run entry
    ],
)
def test_case_ids_rules(tmp_path: Path, change: Any) -> None:
    store = store_of(tmp_path, {"a": {"s": True}})
    entry = json.loads((store / "index.jsonl").read_text())
    (store / "index.jsonl").write_text(json.dumps(change(entry)) + "\n")
    with pytest.raises(InputError) as exc:
        read_history(store)
    assert [d.code for d in exc.value.diagnostics] == ["history_parse"]


def test_compare_entries_are_v2_without_case_ids_and_compared_pairs_only(tmp_path: Path) -> None:
    base = make_run(tmp_path / "b", {"a": {"s": True}})
    cand = make_run(tmp_path / "c", {"a": {"s": False}, "z": {"s": False}})  # z: candidate only
    policy = tmp_path / "p.yaml"
    policy.write_text(
        "version: 1\ncomparison: {min_compared_cases: 1, require_same_cases: false}\n"
        "checks: {max_new_failures: 9}\n"
    )
    report = evalgate.compare(base, cand, policy)
    entry = record_report(report, tmp_path / "h", recorded_at=T0)
    assert entry.history_version == 2
    assert [(f.case_id, f.check) for f in entry.failing_checks] == [("a", "s")]
    assert "case_ids" not in json.loads((tmp_path / "h" / "index.jsonl").read_text())


def test_pre_0_12_report_is_recorded_as_v1_and_still_shows(tmp_path: Path) -> None:
    report = evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml")
    assert report.comparison is not None
    old = report.model_copy(
        update={"comparison": report.comparison.model_copy(update={"failure_groups": None})}
    )
    entry = record_report(old, tmp_path / "h", recorded_at=T0)
    assert entry.history_version == 1 and entry.failing_checks
    _, loaded = load_entry_report(tmp_path / "h", entry.id)
    assert loaded.comparison is not None and loaded.comparison.failure_groups is None
    out = runner.invoke(app, ["history", "show", "1", "--history", str(tmp_path / "h")])
    assert out.exit_code == 0 and "decision: FAIL" in out.output


# --- recurring failures ----------------------------------------------------------------------


def test_failing_of_first_last_streak(tmp_path: Path) -> None:
    store = store_of(
        tmp_path,
        {"a": {"s": False}, "b": {"s": True}},  # id 1
        {"a": {"s": True}, "b": {"s": False}},  # id 2
        {"a": {"s": False}, "b": {"s": False}},  # id 3
        {"a": {"s": False}, "b": {"s": True}},  # id 4
    )
    p = pairs(store)
    assert p[("a", "s")] | {} == {
        "case_id": "a", "check": "s", "failing": 3, "of": 4, "first": 1, "last": 4, "streak": 2,
    }  # fmt: skip
    assert (p[("b", "s")]["failing"], p[("b", "s")]["streak"], p[("b", "s")]["last"]) == (2, 0, 3)
    result = recurring_failures(read_history(store))
    assert [x["case_id"] for x in result["pairs"]] == ["a", "b"]  # streak first


def test_latest_entry_without_the_case_ends_the_streak(tmp_path: Path) -> None:
    store = store_of(tmp_path, {"a": {"s": False}}, {"a": {"s": False}}, {"z": {"s": True}})
    p = pairs(store)[("a", "s")]
    assert (p["failing"], p["of"], p["streak"]) == (2, 2, 0)


def test_errored_case_ends_the_streak(tmp_path: Path) -> None:
    store = store_of(
        tmp_path, {"a": {"s": False}}, {"a": {"s": False}}, {"a": {"s": False}}, errored={2: ("a",)}
    )
    p = pairs(store)[("a", "s")]
    assert (p["failing"], p["of"], p["streak"]) == (2, 2, 0)


def test_min_and_last(tmp_path: Path) -> None:
    store = store_of(tmp_path, {"a": {"s": False}}, {"a": {"s": True}}, {"a": {"s": False}})
    assert ("a", "s") in pairs(store, min_failing=2)
    assert pairs(store, min_failing=3) == {}
    assert pairs(store, last=2, min_failing=1)[("a", "s")]["failing"] == 1
    with pytest.raises(ValueError):
        recurring_failures(read_history(store), min_failing=0)


def test_failing_never_exceeds_of_for_runs(tmp_path: Path) -> None:
    store = store_of(
        tmp_path, {"a": {"s": False, "t": False}}, {"a": {"s": False}, "b": {"t": False}}
    )
    for p in pairs(store, min_failing=1).values():
        assert p["failing"] <= p["of"]


def test_compare_kind_has_null_of_and_skips_invalid(tmp_path: Path) -> None:
    store = tmp_path / "h"
    for _ in range(2):
        record_report(
            evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml"),
            store,
            recorded_at=T0,
        )
    record_report(
        evalgate.compare(tmp_path / "x", tmp_path / "y", tmp_path / "z.yaml"), store, recorded_at=T0
    )
    result = recurring_failures(read_history(store), kind="compare")
    assert result["entries"] == 2  # the INVALID compare is left out
    assert result["pairs"] and all(p["of"] is None for p in result["pairs"])
    assert result["pairs"][0]["streak"] == 2


def test_kind_and_dataset_selection(tmp_path: Path) -> None:
    store = tmp_path / "h"
    record_run(make_run(tmp_path / "a", {"a": {"s": False}}, dataset="x"), store, recorded_at=T0)
    record_run(make_run(tmp_path / "b", {"a": {"s": False}}, dataset="y"), store, recorded_at=T0)
    assert recurring_failures(read_history(store))["mixed_datasets"] is True
    assert pairs(store, dataset="x") == {}
    assert ("a", "s") in pairs(store)


# --- CLI -------------------------------------------------------------------------------------


def test_cli_failures_json_and_table(tmp_path: Path) -> None:
    store = store_of(tmp_path, {"[red]a": {"chk": False}}, {"[red]a": {"chk": False}})
    out = runner.invoke(app, ["history", "failures", "--history", str(store), "--json"])
    assert out.exit_code == 0
    data = json.loads(out.output)
    assert set(data) == {"history_version", "kind", "entries", "pairs", "mixed_datasets"}
    assert set(data["pairs"][0]) == {"case_id", "check", "failing", "of", "first", "last", "streak"}
    table = runner.invoke(app, ["history", "failures", "--history", str(store)])
    assert table.exit_code == 0 and "[red]a" in table.output


def test_cli_failures_exit_codes(tmp_path: Path) -> None:
    store = store_of(tmp_path, {"a": {"s": False}})
    assert (
        runner.invoke(app, ["history", "failures", "--history", str(store), "--min", "0"]).exit_code
        == EXIT_INVALID
    )
    missing = runner.invoke(app, ["history", "failures", "--history", str(tmp_path / "none")])
    assert missing.exit_code == EXIT_INVALID and "history_missing" in missing.output
