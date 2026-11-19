"""`compare --history` and `evalgate history ...` (phase-7 §2, 0.10.0 T3)."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from evalgate import read_history, record_run
from evalgate.cli import EXIT_FAIL, EXIT_INVALID, EXIT_PASS, app
from evalgate.comparison.history import trend

runner = CliRunner()
EXAMPLES = Path(__file__).parent.parent / "examples"
QS = EXAMPLES / "quickstart"
T0 = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def compare(store: Path, *extra: str, candidate: Path = QS / "candidate") -> Any:
    args = ["compare", str(QS / "baseline"), str(candidate), "--policy", str(QS / "evalgate.yaml")]
    return runner.invoke(app, [*args, "--history", str(store), *extra])


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


# --- compare --history -----------------------------------------------------------------------


def test_fail_and_pass_are_recorded_with_their_exit_codes(tmp_path: Path) -> None:
    store = tmp_path / "h"
    assert compare(store, "--label", "v2").exit_code == EXIT_FAIL
    assert compare(store, candidate=QS / "baseline").exit_code == EXIT_PASS
    entries = read_history(store)
    assert [(e.id, e.kind, getattr(e, "status", None)) for e in entries] == [
        (1, "compare", "FAIL"),
        (2, "compare", "PASS"),
    ]
    assert entries[0].label == "v2"


def test_invalid_on_load_is_recorded_and_exits_2(tmp_path: Path) -> None:
    store = tmp_path / "h"
    result = compare(store, candidate=tmp_path / "missing")
    assert result.exit_code == EXIT_INVALID
    [entry] = read_history(store)
    assert entry.status == "INVALID"  # type: ignore[union-attr]


@pytest.mark.parametrize("candidate", ["candidate", "baseline"], ids=["fail", "pass"])
def test_store_failure_exits_2_after_rendering_the_decision(tmp_path: Path, candidate: str) -> None:
    store = tmp_path / "file"
    store.write_text("not a dir")
    result = compare(store, candidate=QS / candidate)
    assert result.exit_code == EXIT_INVALID
    assert "decision:" in result.output
    assert "history_write" in result.output and "history not recorded" in result.output


def test_corrupt_store_is_not_appended(tmp_path: Path) -> None:
    store = tmp_path / "h"
    store.mkdir()
    (store / "index.jsonl").write_text("garbage\n")
    result = compare(store)
    assert result.exit_code == EXIT_INVALID and "history_parse" in result.output
    assert (store / "index.jsonl").read_text() == "garbage\n"


def test_label_needs_history() -> None:
    result = invoke(
        "compare", str(QS / "baseline"), str(QS / "candidate"), "--policy",
        str(QS / "evalgate.yaml"), "--label", "x",
    )  # fmt: skip
    assert result.exit_code == EXIT_INVALID


# --- history add / list / show ---------------------------------------------------------------


def test_add_list_show(tmp_path: Path) -> None:
    store = tmp_path / "h"
    assert invoke("history", "add", str(QS / "baseline"), "--history", str(store)).exit_code == 0
    compare(store)
    listed = invoke("history", "list", "--history", str(store), "--json")
    assert listed.exit_code == 0
    data = json.loads(listed.output)
    assert data["history_version"] == 1
    assert [e["kind"] for e in data["entries"]] == ["run", "compare"]
    last = json.loads(
        invoke("history", "list", "--history", str(store), "--last", "1", "--json").output
    )
    assert [e["id"] for e in last["entries"]] == [2]

    run_show = invoke("history", "show", "1", "--history", str(store))
    assert run_show.exit_code == 0 and "scores.quality" in run_show.output
    run_json = json.loads(invoke("history", "show", "1", "--history", str(store), "--json").output)
    assert run_json["entry"]["kind"] == "run"
    compare_show = invoke("history", "show", "2", "--history", str(store))
    assert compare_show.exit_code == 0 and "decision: FAIL" in compare_show.output
    compare_json = json.loads(
        invoke("history", "show", "2", "--history", str(store), "--json").output
    )
    assert compare_json["report"]["status"] == "FAIL"
    assert compare_json["entry"]["report_sha256"]


def test_add_invalid_run_exits_2(tmp_path: Path) -> None:
    result = invoke("history", "add", str(tmp_path / "nope"), "--history", str(tmp_path / "h"))
    assert result.exit_code == EXIT_INVALID and "run_dir_missing" in result.output
    assert not (tmp_path / "h").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("list",),
        ("show", "1"),
        ("trend", "scores.quality"),
    ],
)
def test_missing_store_exits_2(tmp_path: Path, args: tuple[str, ...]) -> None:
    result = invoke("history", *args, "--history", str(tmp_path / "none"))
    assert result.exit_code == EXIT_INVALID and "history_missing" in result.output


def test_show_unknown_and_tampered(tmp_path: Path) -> None:
    store = tmp_path / "h"
    compare(store)
    assert "history_id" in invoke("history", "show", "7", "--history", str(store)).output
    report = store / "reports" / "000001.json"
    report.write_text(report.read_text().replace("FAIL", "PASS"))
    result = invoke("history", "show", "1", "--history", str(store))
    assert result.exit_code == EXIT_INVALID and "history_id" in result.output


def test_labels_and_ids_are_shown_literally(tmp_path: Path) -> None:
    store = tmp_path / "h"
    label = "[bold red]x[/] line\nnext\x1b[0m"
    assert (
        invoke(
            "history", "add", str(QS / "baseline"), "--history", str(store), "--label", label
        ).exit_code
        == 0
    )
    # `show` prints the label on one line; the narrow test console may wrap it, so unwrap.
    out = invoke("history", "show", "1", "--history", str(store)).output.replace("\n", "")
    assert "x[/]" in out and "line" in out and "next" in out  # markup shown, not applied
    assert "\x1b" not in out  # the raw escape never reaches the terminal


# --- trend -----------------------------------------------------------------------------------


def make_store(tmp_path: Path) -> Path:
    store = tmp_path / "h"
    record_run(QS / "baseline", store, recorded_at=T0, label="b")
    record_run(QS / "candidate", store, recorded_at=T0, label="c")
    other = tmp_path / "other"
    shutil.copytree(QS / "candidate", other)
    manifest = json.loads((other / "run.json").read_text())
    (other / "run.json").write_text(json.dumps(manifest | {"dataset_version": "v4"}))
    record_run(other, store, recorded_at=T0, label="other-dataset")
    compare(store)
    return store


def test_trend_kinds_filters_and_last(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    entries = read_history(store)
    runs = trend(entries, "scores.quality")
    assert [p["label"] for p in runs["points"]] == ["b", "c", "other-dataset"]
    compares = trend(entries, "scores.quality", kind="compare")
    assert [p["id"] for p in compares["points"]] == [4]
    v3 = trend(entries, "scores.quality", dataset="regression-suite@v3")
    assert [p["label"] for p in v3["points"]] == ["b", "c"]
    assert (
        trend(entries, "scores.quality", dataset="regression-suite")["points"][-1]["label"]
        == "other-dataset"
    )
    last = trend(entries, "scores.quality", dataset="regression-suite@v3", last=1)
    assert [p["label"] for p in last["points"]] == ["c"]  # --last after filtering
    assert trend(entries, "scores.quality", application="nope")["points"] == []
    missing = trend(entries, "scores.nope")
    assert (missing["points"], missing["skipped"]) == ([], 3)


def test_trend_cli_warns_on_mixed_datasets_and_round_trips_json(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    mixed = invoke("history", "trend", "scores.quality", "--history", str(store))
    assert mixed.exit_code == 0 and "more than one dataset" in mixed.output
    one = invoke(
        "history",
        "trend",
        "scores.quality",
        "--history",
        str(store),
        "--dataset",
        "regression-suite@v3",
    )
    assert "more than one dataset" not in one.output
    data = json.loads(
        invoke(
            "history",
            "trend",
            "scores.quality",
            "--history",
            str(store),
            "--kind",
            "compare",
            "--json",
        ).output
    )
    assert data["kind"] == "compare" and data["history_version"] == 1
    bad = invoke("history", "trend", "scores.quality", "--history", str(store), "--kind", "both")
    assert bad.exit_code == EXIT_INVALID


def test_help_lists_history_commands() -> None:
    out = invoke("history", "--help").output
    assert all(c in out for c in ("add", "list", "show", "trend"))
    assert "history" in invoke("--help").output
