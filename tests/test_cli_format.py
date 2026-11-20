# ruff: noqa: E501  (long CLI argument lists)
"""`compare --format NAME=PATH` and the Ctrl-C exit code (phase-7 §6, 0.14.0 T2)."""

import sys
from importlib import metadata
from pathlib import Path
from typing import Any

import fake_reporters
import pytest
from test_reporters import FakeDist
from typer.testing import CliRunner

from evalgate.cli import EXIT_FAIL, EXIT_INVALID, EXIT_PASS, app

runner = CliRunner()
QS = Path(__file__).parent.parent / "examples" / "quickstart"
BASE, CAND, POLICY = str(QS / "baseline"), str(QS / "candidate"), str(QS / "evalgate.yaml")
REPORTERS = {
    "text": "fake_reporters:text",
    "raw": "fake_reporters:raw",
    "mut": "fake_reporters:mutating",
    "status": "fake_reporters:status_line",
    "value": "fake_reporters:raise_value",
    "exit": "fake_reporters:raise_exit",
    "ctrlc": "fake_reporters:raise_interrupt",
    "none": "fake_reporters:wrong_type",
    "surrogate": "fake_reporters:bad_text",
    "broken": "fake_broken_import:render",
    "probe": "fake_import_probe:render",
    "oserr": "fake_reporters:raise_oserror",
    "sneaky": "fake_reporters:raise_sneaky",
}


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    dist = FakeDist("fake-reporters", REPORTERS)
    monkeypatch.setattr(metadata, "distributions", lambda **kw: iter([dist]))
    for mod in ("fake_broken_import", "fake_import_probe"):
        sys.modules.pop(mod, None)


def compare(*extra: str, candidate: str = CAND) -> Any:
    return runner.invoke(app, ["compare", BASE, candidate, "--policy", POLICY, *extra])


def test_writes_and_keeps_the_decision_exit_code(tmp_path: Path) -> None:
    out = tmp_path / "r.txt"
    assert compare("--format", f"text={out}").exit_code == EXIT_FAIL
    assert out.read_bytes() == b"status=FAIL\r\nline 2\n"  # str written with no newline translation
    assert compare("--format", f"raw={out}", candidate=BASE).exit_code == EXIT_PASS
    assert out.read_bytes() == b"\x00\xffPASS"


def test_several_candidates_need_n(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "compare",
            BASE,
            CAND,
            BASE,
            "--policy",
            POLICY,
            "--format",
            f"text={tmp_path}/r-{{n}}.txt",
        ],
    )
    assert result.exit_code == EXIT_FAIL
    assert (tmp_path / "r-1.txt").read_text().startswith("status=FAIL")
    assert (tmp_path / "r-2.txt").read_text().startswith("status=PASS")
    bad = runner.invoke(
        app, ["compare", BASE, CAND, BASE, "--policy", POLICY, "--format", f"text={tmp_path}/r.txt"]
    )
    assert bad.exit_code == EXIT_INVALID and "must contain {n}" in bad.output


@pytest.mark.parametrize("value", ["text", "=x", "text="])
def test_malformed_values(value: str) -> None:
    result = compare("--format", value)
    assert result.exit_code == EXIT_INVALID and "NAME=PATH" in result.output


def test_path_may_contain_equals(tmp_path: Path) -> None:
    assert compare("--format", f"text={tmp_path}/a=b.txt").exit_code == EXIT_FAIL
    assert (tmp_path / "a=b.txt").exists()


def test_unknown_name_exits_2_before_comparing(tmp_path: Path) -> None:
    result = compare("--json", str(tmp_path / "r.json"), "--format", f"nope={tmp_path}/x")
    assert result.exit_code == EXIT_INVALID and "no reporter named 'nope'" in result.output
    assert list(tmp_path.iterdir()) == []


def test_lazy_loading(tmp_path: Path) -> None:
    assert compare().exit_code == EXIT_FAIL  # a plain compare reads no plugin
    assert (
        compare("--format", f"text={tmp_path}/a.txt").exit_code == EXIT_FAIL
    )  # `broken` not named
    assert "fake_broken_import" not in sys.modules


def test_import_time_code_runs_after_trusted_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVALGATE_PROBE_DIR", str(tmp_path))
    result = compare(
        "--json", str(tmp_path / "r.json"), "--summary", str(tmp_path / "s.json"),
        "--history", str(tmp_path / "h"), "--format", f"probe={tmp_path}/p.txt",
    )  # fmt: skip
    assert result.exit_code == EXIT_FAIL, result.output
    probe = sys.modules["fake_import_probe"]
    assert {"r.json", "s.json", "h"} <= set(probe.SEEN) and "p.txt" not in probe.SEEN


@pytest.mark.parametrize("name", ["value", "none", "surrogate", "broken"])
def test_failures_exit_2_after_rendering(tmp_path: Path, name: str) -> None:
    result = compare(
        "--json",
        str(tmp_path / "r.json"),
        "--format",
        f"{name}={tmp_path}/p.txt",
        "--format",
        f"text={tmp_path}/later.txt",
    )
    assert result.exit_code == EXIT_INVALID
    assert "decision: FAIL" in result.output and f"reporter '{name}'" in result.output
    assert "SECRET" not in result.output  # the exception message is not shown
    assert (tmp_path / "r.json").exists()
    assert not (tmp_path / "p.txt").exists() and not (tmp_path / "later.txt").exists()


def test_system_exit_zero_cannot_turn_a_fail_into_a_pass(tmp_path: Path) -> None:
    result = compare("--format", f"exit={tmp_path}/p.txt")
    assert result.exit_code == EXIT_INVALID and "SystemExit" in result.output


def test_ctrl_c_in_a_reporter_exits_2(tmp_path: Path) -> None:
    result = compare("--format", f"ctrlc={tmp_path}/p.txt")
    assert result.exit_code == EXIT_INVALID and "interrupted: no decision" in result.output


def test_ctrl_c_in_the_command_body_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    import evalgate.cli as cli

    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "compare_many", interrupted)
    result = compare()
    assert "interrupted: no decision" in result.output
    assert result.exit_code == EXIT_INVALID  # Typer alone would exit 1, the FAIL code


def test_mutation_does_not_reach_the_next_reporter_or_the_terminal(tmp_path: Path) -> None:
    result = compare("--format", f"mut={tmp_path}/m.txt", "--format", f"status={tmp_path}/s.txt")
    assert result.exit_code == EXIT_FAIL
    assert (tmp_path / "s.txt").read_text() == "FAIL comparison=True metrics=3\n"  # nested intact
    assert "decision: FAIL" in result.output


def test_reporter_runs_for_an_invalid_candidate(tmp_path: Path) -> None:
    result = compare("--format", f"status={tmp_path}/s.txt", candidate=str(tmp_path / "missing"))
    assert result.exit_code == EXIT_INVALID
    assert (tmp_path / "s.txt").read_text() == "INVALID comparison=False metrics=-1\n"


def test_each_reporter_loads_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from evalgate.io import reporters as registry

    loads: list[str] = []
    real = registry.load

    def spy(ep: metadata.EntryPoint) -> Any:
        loads.append(ep.name)
        return real(ep)

    monkeypatch.setattr(registry, "load", spy)
    fake_reporters.CALLS.clear()
    args = [
        "compare",
        BASE,
        CAND,
        BASE,
        "--policy",
        POLICY,
        "--format",
        f"text={tmp_path}/t-{{n}}.txt",
    ]
    assert runner.invoke(app, args).exit_code == EXIT_FAIL
    assert loads == ["text"] and fake_reporters.CALLS == ["text", "text"]


def test_a_reporters_oserror_is_a_reporter_failure(tmp_path: Path) -> None:
    result = compare("--format", f"oserr={tmp_path}/p.txt")
    assert result.exit_code == EXIT_INVALID
    assert (
        "reporter 'oserr' failed: FileNotFoundError" in result.output
        and "SECRET" not in result.output
    )


def test_write_failure_names_the_path(tmp_path: Path) -> None:
    result = compare("--format", f"text={tmp_path}/missing-dir/p.txt")
    assert result.exit_code == EXIT_INVALID
    assert "cannot write" in result.output and "missing-dir" in result.output.replace("\n", "")


def test_formatting_the_exception_cannot_pick_the_exit_code(tmp_path: Path) -> None:
    result = compare("--format", f"sneaky={tmp_path}/p.txt")
    assert result.exit_code == EXIT_INVALID and "reporter 'sneaky' failed" in result.output


@pytest.mark.parametrize("failing", ["json", "history"])
def test_reporters_skipped_after_a_trusted_write_fails(tmp_path: Path, failing: str) -> None:
    if failing == "json":
        extra = ["--json", str(tmp_path / "missing-dir" / "r.json")]
    else:
        (tmp_path / "h").write_text("not a directory")
        extra = ["--history", str(tmp_path / "h")]
    result = compare(*extra, "--format", f"text={tmp_path}/p.txt")
    assert result.exit_code == EXIT_INVALID
    assert not (tmp_path / "p.txt").exists()


def test_plain_compare_reads_no_entry_point_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs: object) -> Any:
        raise AssertionError("entry point metadata was read")

    monkeypatch.setattr(metadata, "distributions", boom)
    assert compare().exit_code == EXIT_FAIL
