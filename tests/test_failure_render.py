"""Failure groups in the terminal and Markdown reports (phase-7 §4, 0.12.0 T2)."""

from io import StringIO
from typing import Any

from conftest import MANIFEST
from rich.console import Console

from evalgate.engine import evaluate
from evalgate.models import Policy, Report
from evalgate.models.run import CaseResult, Run, RunManifest
from evalgate.reporting.markdown import render_markdown
from evalgate.reporting.terminal import render

POLICY = Policy.model_validate(
    {"version": 1, "comparison": {"min_compared_cases": 1}, "checks": {"max_new_failures": 99}}
)


def run(*cases: dict[str, Any]) -> Run:
    return Run(
        manifest=RunManifest(**MANIFEST),
        cases=[CaseResult(**({"status": "ok"} | c)) for c in cases],
    )


def case(case_id: str, category: str | None = None, **checks: bool) -> dict[str, Any]:
    meta = {"metadata": {"category": category}} if category else {}
    return {"case_id": case_id, "checks": checks} | meta


def terminal(report: Report) -> str:
    console = Console(file=StringIO(), width=200)
    render(report, console)
    return console.file.getvalue()  # type: ignore[attr-defined,no-any-return]


def test_top_five_with_counts_in_terminal_and_markdown() -> None:
    names = [f"check{i}" for i in range(7)]
    base = run(*(case(f"c{n}", "cat", **dict.fromkeys(names, True)) for n in range(3)))
    cand = run(*(case(f"c{n}", "cat", **dict.fromkeys(names, False)) for n in range(3)))
    report = evaluate(base, cand, POLICY)
    out = terminal(report)
    assert "failure groups (top 5)" in out
    assert "check0 / cat: 3 = 3 + 0 + 0" in out
    assert "check5 / cat" not in out  # only the top 5
    md = render_markdown(report)
    assert "### Failure groups" in md and md.count("/ cat` | 3 | 3 | 0 | 0 |") == 5


def test_falls_back_to_by_check_without_categories() -> None:
    report = evaluate(run(case("a", s=True)), run(case("a", s=False)), POLICY)
    assert "  s: 1 = 1 + 0 + 0" in terminal(report)
    assert "| `s` | 1 | 1 | 0 | 0 |" in render_markdown(report)


def test_nothing_shown_when_nothing_fails() -> None:
    report = evaluate(run(case("a", s=True)), run(case("a", s=True)), POLICY)
    assert "failure groups" not in terminal(report)
    assert "Failure groups" not in render_markdown(report)


def test_nothing_shown_for_an_invalid_report_with_a_comparison() -> None:
    policy = Policy.model_validate(
        {
            "version": 1,
            "comparison": {"min_compared_cases": 1},
            "metrics": {
                "q": {"path": "scores.q", "direction": "maximize", "max_mean_regression_pct": 5}
            },
        }
    )
    base = run(case("a", s=True) | {"scores": {"q": 0}})
    cand = run(case("a", s=False) | {"scores": {"q": 0}})
    report = evaluate(base, cand, policy)
    assert report.status == "INVALID" and report.comparison is not None
    assert "failure groups" not in terminal(report)
    assert "Failure groups" not in render_markdown(report)


def test_names_are_escaped() -> None:
    report = evaluate(
        run(case("a", "[red]cat[/]", **{"[bold]chk": True})),
        run(case("a", "[red]cat[/]", **{"[bold]chk": False})),
        POLICY,
    )
    out = terminal(report)
    assert "[bold]chk / [red]cat[/]" in out
    assert "`[bold]chk / [red]cat[/]`" in render_markdown(report)
