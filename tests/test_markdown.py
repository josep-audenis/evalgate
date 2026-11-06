import re
from pathlib import Path

import pytest
from conftest import case, make_policy, make_run
from typer.testing import CliRunner

from evalgate import compare
from evalgate.cli import EXIT_FAIL, app
from evalgate.engine import evaluate, invalid_report
from evalgate.models import Diagnostic
from evalgate.reporting.markdown import _c, render_markdown

EXAMPLE = Path(__file__).parent.parent / "examples" / "quickstart"
FAILING = {"safety_valid": False, "schema_valid": True}


def cells(row: str) -> int:
    """Table cells in a row, ignoring escaped pipes."""
    return len(re.findall(r"(?<!\\)\|", row)) - 1


def test_failure_summary_lists_failed_rules_first() -> None:
    base = [case("a"), case("b")]
    cand = [case("a"), case("b", 0.5, checks=FAILING)]
    md = render_markdown(evaluate(make_run(base), make_run(cand), make_policy()))
    assert md.startswith("## EvalGate: ❌ FAIL\n")
    rules = [line for line in md.splitlines() if line.startswith(("| ❌", "| ✅"))]
    first_pass = next(i for i, r in enumerate(rules) if r.startswith("| ✅"))
    assert all(r.startswith("| ❌") for r in rules[:first_pass])
    assert any("`checks.required.safety_valid`" in r for r in rules[:first_pass])
    assert "### Metrics" in md and "<summary>Newly failing checks (1)</summary>" in md


def test_pass_summary() -> None:
    md = render_markdown(evaluate(make_run([case("a")]), make_run([case("a")]), make_policy()))
    assert md.startswith("## EvalGate: ✅ PASS\n") and "(0 failed of" in md
    assert "| `quality` | max | 0.9 | 0.9 | +0 | +0.00% | [+0, +0] |" in md


@pytest.mark.parametrize(
    "text",
    [
        "@octocat ping",  # mention: would notify a GitHub user
        "#2 GH-1 owner/repo#1",  # issue and cross-repo references
        "https://evil.example www.evil.example a@b.example",  # autolinks
        "$\\color{red}x$ $$y$$",  # math
        "<img src=x onerror=alert(1)> [l](http://e) **b**",
        "x|y\nz",
        "`one` ``two``",
        "1) ordered",
    ],
)
def test_untrusted_text_is_a_literal_code_span(text: str) -> None:
    span = _c(text)
    fence = re.match(r"`+", span)
    assert fence and span.endswith(fence[0])  # one code span: GitHub renders it literally
    inner = span[len(fence[0]) : -len(fence[0])].strip(" ")
    assert inner == " ".join(text.split())  # content kept, on one line
    assert fence[0] not in inner  # the fence is longer than any backtick run inside


def test_untrusted_text_cannot_break_tables() -> None:
    nasty = "x|y\n<img src=x>`a|b`"
    base, cand = [case(nasty), case("b")], [case(nasty, 0.1, checks=FAILING), case("b")]
    md = render_markdown(evaluate(make_run(base, run_id="@octocat"), make_run(cand), make_policy()))
    for line in md.splitlines():
        if line.startswith("| "):
            assert cells(line) in (3, 7), line  # rules table, metrics table
    assert "`@octocat`" in md  # run ids are code too


def test_invisible_characters_stripped_and_long_text_capped() -> None:
    assert _c("bidi‮exe.txt​") == "`bidiexe.txt`"
    assert len(_c("x" * 5000)) < 310 and _c("x" * 5000).endswith("…`")
    assert _c("") == "(empty)"


def test_huge_failure_stays_under_github_comment_limit() -> None:
    ids = [f"case-{i:05}" for i in range(3000)]
    base = [case(i) for i in ids]
    cand = [case(i, 0.1, checks=FAILING) for i in ids]
    md = render_markdown(evaluate(make_run(base), make_run(cand), make_policy()))
    assert len(md) < 65_536
    assert "<summary>Regressed cases (3000, first 10)</summary>" in md


def test_invalid_report_without_comparison() -> None:
    report = invalid_report([Diagnostic(level="error", code="file_missing", message="no run.json")])
    md = render_markdown(report)
    assert md.startswith("## EvalGate: ⚠️ INVALID")
    assert "- **error** `file_missing`: `no run.json`" in md
    assert "### Rules" not in md and "### Metrics" not in md


def test_long_lists_truncated_in_details() -> None:
    base = [case(f"c{i:02}") for i in range(15)]
    cand = [case(f"c{i:02}", 0.5) for i in range(15)]
    md = render_markdown(evaluate(make_run(base), make_run(cand), make_policy()))
    assert "<summary>Regressed cases (15, first 10)</summary>" in md
    assert md.count("\n- `c") == 10


def test_cli_and_sdk_write_the_same_markdown(tmp_path: Path) -> None:
    args = [str(EXAMPLE / "baseline"), str(EXAMPLE / "candidate")]
    policy = str(EXAMPLE / "evalgate.yaml")
    result = CliRunner().invoke(
        app, ["compare", *args, "--policy", policy, "--markdown", str(tmp_path / "cli.md")]
    )
    assert result.exit_code == EXIT_FAIL
    compare(*args, policy=policy).to_markdown(tmp_path / "sdk.md")
    cli, sdk = ((tmp_path / f).read_text(encoding="utf-8") for f in ("cli.md", "sdk.md"))
    assert cli == sdk and "`abuse-001`: `safety_valid`" in cli
