import re
from pathlib import Path

from conftest import case, make_policy, make_run
from typer.testing import CliRunner

import evalgate
from evalgate.cli import EXIT_FAIL, EXIT_INVALID, EXIT_PASS, app
from evalgate.engine import evaluate, invalid_report
from evalgate.io import load_policy
from evalgate.models import Diagnostic
from evalgate.reporting.html import render_html

runner = CliRunner()
EXAMPLE = Path(__file__).parent.parent / "examples" / "quickstart"


def test_html_is_standalone_escaped_and_explains_failure() -> None:
    base = [case("a"), case("<b>&x")]
    cand = [case("a"), case("<b>&x", 0.5, checks={"safety_valid": False, "schema_valid": True})]
    html = render_html(evaluate(make_run(base), make_run(cand), make_policy()))
    assert html.startswith("<!doctype html>")
    assert not re.search(r"(src|href)=|<script|https?://", html)  # no external resources
    assert "<b>&x" not in html and "&lt;b&gt;&amp;x" in html
    for section in ("Failed policies", "Metrics", "Policy rules", "Regressed cases"):
        assert f"<h2>{section}</h2>" in html
    assert "checks.required.safety_valid" in html
    assert 'class="status FAIL"' in html


def test_html_for_invalid_report_without_comparison() -> None:
    report = invalid_report([Diagnostic(level="error", code="file_missing", message="no run.json")])
    html = render_html(report)
    assert 'class="status INVALID"' in html and "no run.json" in html


def test_html_is_deterministic() -> None:
    report = evaluate(make_run([case("a")]), make_run([case("a", 0.8)]), make_policy())
    assert render_html(report) == render_html(report)


def test_cli_report_matches_sdk(tmp_path: Path) -> None:
    args = [str(EXAMPLE / "baseline"), str(EXAMPLE / "candidate")]
    policy = str(EXAMPLE / "evalgate.yaml")
    result = runner.invoke(
        app, ["compare", *args, "--policy", policy, "--report", str(tmp_path / "cli.html")]
    )
    assert result.exit_code == EXIT_FAIL
    evalgate.compare(*args, policy).to_html(tmp_path / "sdk.html")
    assert (tmp_path / "cli.html").read_text() == (tmp_path / "sdk.html").read_text()


def test_init_writes_valid_policy_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "evalgate.yaml"
    assert runner.invoke(app, ["init", str(path)]).exit_code == EXIT_PASS
    policy = load_policy(path)
    assert set(policy.metrics) == {"quality", "latency"}
    assert runner.invoke(app, ["init", str(path)]).exit_code == EXIT_INVALID
    path.write_text("edited")
    assert runner.invoke(app, ["init", str(path), "--force"]).exit_code == EXIT_PASS
    assert load_policy(path) == policy
