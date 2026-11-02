"""Compact terminal summary for local use and CI logs."""

from rich.console import Console
from rich.table import Table

from evalgate.models import Diagnostic, Report

_TOP = 10
_STATUS_STYLE = {"PASS": "bold green", "FAIL": "bold red", "INVALID": "bold yellow"}


def render_diagnostics(diagnostics: list[Diagnostic], console: Console) -> None:
    for d in diagnostics:
        style = "red" if d.level == "error" else "yellow"
        console.print(f"[{style}]{d.level}[/] [dim]{d.code}[/] {d.message}", highlight=False)


def render(report: Report, console: Console) -> None:
    b, c = report.baseline, report.candidate
    if b and c:
        console.print(
            f"[bold]{b.run_id}[/] -> [bold]{c.run_id}[/]  "
            f"dataset {c.dataset_id}@{c.dataset_version}  evaluator {c.evaluator_version}",
            highlight=False,
        )

    cmp = report.comparison
    if cmp:
        console.print(
            f"cases: {cmp.matched_cases} matched, {cmp.compared_cases} compared, "
            f"{len(cmp.baseline_errors)} baseline errors, "
            f"{len(cmp.candidate_errors)} candidate errors",
            highlight=False,
        )
        if cmp.metrics:
            table = Table(box=None, pad_edge=False)
            for col in ("metric", "direction", "baseline", "candidate", "delta", "delta %"):
                table.add_column(col, justify="left" if col in ("metric", "direction") else "right")
            for m in cmp.metrics:
                pct = "n/a" if m.delta_pct is None else f"{m.delta_pct:+.2f}%"
                table.add_row(
                    m.name,
                    m.direction,
                    f"{m.baseline_mean:.6g}",
                    f"{m.candidate_mean:.6g}",
                    f"{m.delta_abs:+.6g}",
                    pct,
                )
            console.print(table)
        if cmp.regressed_cases:
            console.print(f"regressed cases ({len(cmp.regressed_cases)}, top {_TOP}):")
            for d in cmp.regressed_cases[:_TOP]:
                console.print(
                    f"  {d.case_id} {d.metric}: {d.baseline:.6g} -> {d.candidate:.6g}",
                    highlight=False,
                )
        if cmp.newly_failing_checks:
            console.print(f"newly failing checks ({len(cmp.newly_failing_checks)}):")
            for ch in cmp.newly_failing_checks[:_TOP]:
                console.print(f"  {ch.case_id}: {ch.check}", highlight=False)

    for r in report.rules:
        mark = "[green]ok  [/]" if r.passed else "[red]FAIL[/]"
        console.print(f"{mark} {r.rule}: {r.message}", highlight=False)
    render_diagnostics(report.diagnostics, console)
    console.print(f"decision: [{_STATUS_STYLE[report.status]}]{report.status}[/]")
