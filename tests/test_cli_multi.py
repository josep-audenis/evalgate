"""`evalgate compare BASELINE CANDIDATE...` (phase-7 §3, 0.11.0 T2)."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from evalgate import compare, read_history
from evalgate.cli import EXIT_FAIL, EXIT_INVALID, EXIT_PASS, app

runner = CliRunner()
QS = Path(__file__).parent.parent / "examples" / "quickstart"
BASE, CAND, POLICY = str(QS / "baseline"), str(QS / "candidate"), str(QS / "evalgate.yaml")


def run(*args: str) -> Any:
    return runner.invoke(app, ["compare", *args])


def test_single_candidate_output_is_unchanged(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    result = run(BASE, CAND, "--policy", POLICY, "--json", str(out))
    assert result.exit_code == EXIT_FAIL
    assert out.read_text() == compare(BASE, CAND, POLICY).model_dump_json(indent=2) + "\n"
    assert "candidate 1:" not in result.output and "candidates" not in result.output


def test_single_candidate_keeps_a_literal_n(tmp_path: Path) -> None:
    result = run(BASE, CAND, "--policy", POLICY, "--json", str(tmp_path / "r-{n}.json"))
    assert result.exit_code == EXIT_FAIL
    assert (tmp_path / "r-{n}.json").exists()


def test_n_candidates_write_n_files_and_aggregate_exit_code(tmp_path: Path) -> None:
    result = run(
        BASE, CAND, BASE, "--policy", POLICY,
        "--json", str(tmp_path / "r-{n}.json"), "--markdown", str(tmp_path / "m-{n}.md"),
    )  # fmt: skip
    assert result.exit_code == EXIT_FAIL  # one FAIL, one PASS
    for n, cand in ((1, CAND), (2, BASE)):
        expected = compare(BASE, cand, POLICY).model_dump_json(indent=2) + "\n"
        assert (tmp_path / f"r-{n}.json").read_text() == expected
        assert (tmp_path / f"m-{n}.md").exists()
    assert "candidate 1:" in result.output and "candidate 2:" in result.output


def test_all_pass_exits_0_and_any_invalid_exits_2(tmp_path: Path) -> None:
    assert run(BASE, BASE, BASE, "--policy", POLICY).exit_code == EXIT_PASS
    result = run(BASE, BASE, str(tmp_path / "missing"), CAND, "--policy", POLICY)
    assert result.exit_code == EXIT_INVALID


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{n}-{n}.json", ["1-1.json", "2-2.json"]),
        ("r-{run}-{n}.json", ["r-{run}-1.json", "r-{run}-2.json"]),
        ("d{n}/r.json", ["d1/r.json", "d2/r.json"]),
    ],
)
def test_n_substitution(tmp_path: Path, template: str, expected: list[str]) -> None:
    for name in expected:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)  # parents aren't created
    result = run(BASE, CAND, BASE, "--policy", POLICY, "--json", str(tmp_path / template))
    assert result.exit_code == EXIT_FAIL, result.output
    assert all((tmp_path / name).exists() for name in expected)


def test_path_without_n_exits_2_and_writes_nothing(tmp_path: Path) -> None:
    result = run(BASE, CAND, BASE, "--policy", POLICY, "--report", str(tmp_path / "r.html"))
    assert result.exit_code == EXIT_INVALID and "--report must contain {n}" in result.output
    assert list(tmp_path.iterdir()) == []


def test_summary_with_one_and_with_n(tmp_path: Path) -> None:
    assert run(BASE, CAND, "--policy", POLICY, "--summary", str(tmp_path / "1.json")).exit_code == 1
    one = json.loads((tmp_path / "1.json").read_text())
    assert [c["status"] for c in one["candidates"]] == ["FAIL"] and one["ranking"] is None
    run(
        BASE,
        CAND,
        BASE,
        "--policy",
        POLICY,
        "--rank-by",
        "quality",
        "--summary",
        str(tmp_path / "n.json"),
    )
    many = json.loads((tmp_path / "n.json").read_text())
    assert many["exit_code"] == 1 and many["rank_by"] == "quality"
    assert [(r["rank"], r["n"]) for r in many["ranking"]] == [(1, 1), (2, 2)]


def test_rank_by_shows_the_table_even_for_one_candidate() -> None:
    result = run(BASE, CAND, "--policy", POLICY, "--rank-by", "quality")
    assert "candidate 1:" in result.output and "ranked by quality" in result.output


def test_unknown_rank_by_exits_2_before_comparing(tmp_path: Path) -> None:
    result = run(
        BASE, CAND, "--policy", POLICY, "--rank-by", "nope", "--json", str(tmp_path / "r.json")
    )
    assert result.exit_code == EXIT_INVALID and "not a policy metric" in result.output
    assert not (tmp_path / "r.json").exists()


def test_rank_by_with_an_invalid_policy(tmp_path: Path) -> None:
    result = run(BASE, CAND, "--policy", str(tmp_path / "none.yaml"), "--rank-by", "nope")
    assert result.exit_code == EXIT_INVALID and "file_missing" in result.output


def test_write_failure_on_candidate_2_of_3(tmp_path: Path) -> None:
    (tmp_path / "r-2.json").mkdir()  # can't write a file over a directory
    result = run(BASE, CAND, BASE, CAND, "--policy", POLICY, "--json", str(tmp_path / "r-{n}.json"))
    assert result.exit_code == EXIT_INVALID
    assert (tmp_path / "r-1.json").is_file() and not (tmp_path / "r-3.json").exists()
    assert "candidate 3:" in result.output and "cannot write" in result.output


def test_summary_write_failure(tmp_path: Path) -> None:
    (tmp_path / "s.json").mkdir()
    result = run(BASE, CAND, BASE, "--policy", POLICY, "--summary", str(tmp_path / "s.json"))
    assert result.exit_code == EXIT_INVALID and "cannot write" in result.output
    assert "exit code: 1" in result.output  # the decision is still shown


def test_history_records_n_entries_in_order(tmp_path: Path) -> None:
    store = tmp_path / "h"
    result = run(BASE, CAND, BASE, "--policy", POLICY, "--history", str(store), "--label", "rc")
    assert result.exit_code == EXIT_FAIL
    entries = read_history(store)
    assert [(e.id, e.status, e.label) for e in entries] == [(1, "FAIL", "rc"), (2, "PASS", "rc")]  # type: ignore[union-attr]


def test_options_between_positionals(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["compare", BASE, "--policy", POLICY, CAND, "--summary", str(tmp_path / "s.json"), BASE],
    )
    assert result.exit_code == EXIT_FAIL
    assert len(json.loads((tmp_path / "s.json").read_text())["candidates"]) == 2


def test_candidate_paths_are_escaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    shutil.copytree(QS / "baseline", tmp_path / "[bold]x")
    monkeypatch.chdir(tmp_path)  # a short relative path, so the narrow console doesn't wrap it
    result = run(BASE, "[bold]x", BASE, "--policy", POLICY)
    assert result.exit_code == EXIT_PASS
    assert "candidate 1: [bold]x" in result.output


def test_help_names_candidates() -> None:
    assert "CANDIDATE..." in runner.invoke(app, ["compare", "--help"]).output


def test_single_candidate_artifacts_terminal_and_history_are_byte_identical(tmp_path: Path) -> None:
    from io import StringIO

    from rich.console import Console

    from evalgate.io.history import _report_text
    from evalgate.reporting.html import render_html
    from evalgate.reporting.markdown import render_markdown
    from evalgate.reporting.terminal import render

    report = compare(BASE, CAND, POLICY)
    result = run(
        BASE, CAND, "--policy", POLICY, "--json", str(tmp_path / "r.json"),
        "--report", str(tmp_path / "r.html"), "--markdown", str(tmp_path / "r.md"),
        "--history", str(tmp_path / "h"),
    )  # fmt: skip
    assert result.exit_code == EXIT_FAIL
    assert (tmp_path / "r.html").read_text(encoding="utf-8") == render_html(report)
    assert (tmp_path / "r.md").read_text(encoding="utf-8") == render_markdown(report)
    console = Console(file=StringIO(), width=80)
    render(report, console)
    words = console.file.getvalue().split()  # type: ignore[attr-defined]
    assert result.output.split() == words  # same text; line wrapping depends on width
    [entry] = read_history(tmp_path / "h")
    import hashlib

    assert entry.report_sha256 == hashlib.sha256(_report_text(report).encode()).hexdigest()  # type: ignore[union-attr]


def test_any_writer_exception_still_renders_every_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import evalgate.cli as cli

    def boom(report: object, path: Path) -> None:
        raise ValueError("renderer bug")

    monkeypatch.setattr(cli, "write_html_report", boom)
    result = run(
        BASE, CAND, BASE, "--policy", POLICY,
        "--json", str(tmp_path / "r-{n}.json"), "--report", str(tmp_path / "r-{n}.html"),
    )  # fmt: skip
    assert result.exit_code == EXIT_INVALID
    assert "candidate 2:" in result.output and "decision exit code: 1" in result.output
    assert "cannot write" in result.output and str(tmp_path / "r-1.html") in result.output.replace(
        "\n", ""
    )
    assert (tmp_path / "r-1.json").exists() and not (tmp_path / "r-2.json").exists()


def test_history_failure_partway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evalgate.cli as cli
    from evalgate.io import InputError
    from evalgate.models import Diagnostic

    real = cli.record_report
    calls = {"n": 0}

    def flaky(report: Any, store: Path, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise InputError([Diagnostic(level="error", code="history_write", message="[red]boom")])
        return real(report, store, **kwargs)

    monkeypatch.setattr(cli, "record_report", flaky)
    store = tmp_path / "h"
    result = run(BASE, CAND, BASE, CAND, "--policy", POLICY, "--history", str(store))
    assert result.exit_code == EXIT_INVALID
    assert [e.id for e in read_history(store)] == [1]
    assert "only 1 of 3 recorded" in result.output and "[red]boom" in result.output
    assert "candidate 3:" in result.output


def test_case_count_warning_is_printed(tmp_path: Path) -> None:
    import shutil

    errored = tmp_path / "errored"
    shutil.copytree(QS / "baseline", errored)
    lines = (errored / "cases.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    lines[0] = json.dumps({"case_id": first["case_id"], "status": "error", "error": "timeout"})
    (errored / "cases.jsonl").write_text("\n".join(lines) + "\n")
    result = run(BASE, BASE, str(errored), "--policy", POLICY, "--rank-by", "quality")
    assert "different numbers of cases" in result.output.replace("\n", " ")
