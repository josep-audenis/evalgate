import re
from pathlib import Path

from conftest import case, make_policy, make_run
from typer.testing import CliRunner

from evalgate import compare
from evalgate.cli import EXIT_FAIL, app
from evalgate.engine import evaluate, invalid_report
from evalgate.models import Diagnostic
from evalgate.reporting.markdown import render_markdown

EXAMPLE = Path(__file__).parent.parent / "examples" / "quickstart"


def cells(row: str) -> int:
    """Table cells in a row, ignoring escaped pipes."""
    return len(re.findall(r"(?<!\\)\|", row)) - 1


def test_failure_summary_lists_failed_rules_first() -> None:
    base = [case("a"), case("b")]
    cand = [case("a"), case("b", 0.5, checks={"safety_valid": False, "schema_valid": True})]
    md = render_markdown(evaluate(make_run(base), make_run(cand), make_policy()))
    assert md.startswith("## EvalGate: ❌ FAIL\n")
    rules = [line for line in md.splitlines() if line.startswith("| ❌") or line.startswith("| ✅")]
    first_pass = next(i for i, r in enumerate(rules) if r.startswith("| ✅"))
    assert all(r.startswith("| ❌") for r in rules[:first_pass])
    assert any("checks.required.safety\\_valid" in r for r in rules[:first_pass])
    assert "### Metrics" in md and "<summary>Newly failing checks (1)</summary>" in md


def test_untrusted_text_cannot_break_tables_or_inject_markup() -> None:
    nasty = "x|y\n<img src=x onerror=alert(1)>[link](http://e)**b**"
    base = [case(nasty), case("b")]
    cand = [case(nasty, 0.1), case("b")]
    md = render_markdown(evaluate(make_run(base), make_run(cand), make_policy()))
    assert "<img" not in md and "[link](http" not in md and "**b**" not in md
    assert "\\[link\\](http" in md and "&lt;img" in md
    for line in md.splitlines():
        if line.startswith("| "):
            assert cells(line) in (3, 7), line  # rules table, metrics table
    regressed = next(line for line in md.splitlines() if line.startswith("- x"))
    assert "\n" not in regressed and "\\|" in regressed


def test_invalid_report_without_comparison() -> None:
    report = invalid_report([Diagnostic(level="error", code="file_missing", message="no run.json")])
    md = render_markdown(report)
    assert md.startswith("## EvalGate: ⚠️ INVALID")
    assert "- **error** file\\_missing: no run.json" in md
    assert "### Rules" not in md and "### Metrics" not in md


def test_long_lists_truncated_in_details() -> None:
    base = [case(f"c{i:02}") for i in range(15)]
    cand = [case(f"c{i:02}", 0.5) for i in range(15)]
    md = render_markdown(evaluate(make_run(base), make_run(cand), make_policy()))
    assert "<summary>Regressed cases (15, first 10)</summary>" in md
    assert md.count("\n- c") == 10


def test_cli_and_sdk_write_the_same_markdown(tmp_path: Path) -> None:
    args = [str(EXAMPLE / "baseline"), str(EXAMPLE / "candidate")]
    policy = str(EXAMPLE / "evalgate.yaml")
    result = CliRunner().invoke(
        app, ["compare", *args, "--policy", policy, "--markdown", str(tmp_path / "cli.md")]
    )
    assert result.exit_code == EXIT_FAIL
    compare(*args, policy=policy).to_markdown(tmp_path / "sdk.md")
    cli, sdk = ((tmp_path / f).read_text(encoding="utf-8") for f in ("cli.md", "sdk.md"))
    assert cli == sdk and "abuse-001: safety\\_valid" in cli
