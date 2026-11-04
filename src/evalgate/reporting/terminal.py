"""Compact terminal summary for local use and CI logs."""

from rich.console import Console
from rich.markup import escape as e  # every input-derived string goes through this
from rich.table import Table

from evalgate.models import Diagnostic, Report

_TOP = 10
_STATUS_STYLE = {"PASS": "bold green", "FAIL": "bold red", "INVALID": "bold yellow"}


def render_diagnostics(diagnostics: list[Diagnostic], console: Console) -> None:
    for d in diagnostics:
        style = "red" if d.level == "error" else "yellow"
        console.print(f"[{style}]{d.level}[/] [dim]{e(d.code)}[/] {e(d.message)}", highlight=False)


def render(report: Report, console: Console) -> None:
    b, c = report.baseline, report.candidate
    if b and c:
        console.print(
            f"[bold]{e(b.run_id)}[/] -> [bold]{e(c.run_id)}[/]  "
            f"dataset {e(c.dataset_id)}@{e(c.dataset_version)}  "
            f"evaluator {e(c.evaluator_version)}",
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
            # 4 significant digits keep the table within 80 columns (CI logs); JSON is exact.
            cols = ("metric", "goal", "baseline", "candidate", "delta", "delta %", "CI of delta")
            for col in cols:
                table.add_column(col, justify="left" if col in ("metric", "goal") else "right")
            for m in cmp.metrics:
                pct = "n/a" if m.delta_pct is None else f"{m.delta_pct:+.2f}%"
                table.add_row(
                    e(m.name),
                    m.direction[:3],
                    f"{m.baseline_mean:.4g}",
                    f"{m.candidate_mean:.4g}",
                    f"{m.delta_abs:+.4g}",
                    pct,
                    f"[{m.ci.low:+.4g}, {m.ci.high:+.4g}]",
                )
            console.print(table)
            ci = cmp.metrics[0].ci
            console.print(
                f"[dim]CI: {ci.confidence:.0%} interval of the mean delta ({ci.method}, "
                f"{cmp.compared_cases} paired cases)[/]",
                highlight=False,
            )
        if cmp.regressed_cases:
            console.print(f"regressed cases ({len(cmp.regressed_cases)}, top {_TOP}):")
            for d in cmp.regressed_cases[:_TOP]:
                console.print(
                    f"  {e(d.case_id)} {e(d.metric)}: {d.baseline:.6g} -> {d.candidate:.6g}",
                    highlight=False,
                )
        flipped = [s for s in cmp.check_stats if s.newly_failing or s.newly_passing]
        if flipped:
            console.print("check flips (one-sided McNemar p):")
            for s in flipped:
                console.print(
                    f"  {e(s.check)}: {s.newly_failing} newly failing, "
                    f"{s.newly_passing} newly passing, p={s.p_value:.3g}",
                    highlight=False,
                )
        if cmp.newly_failing_checks:
            console.print(f"newly failing checks ({len(cmp.newly_failing_checks)}):")
            for ch in cmp.newly_failing_checks[:_TOP]:
                console.print(f"  {e(ch.case_id)}: {e(ch.check)}", highlight=False)

    for r in report.rules:
        mark = "[green]ok  [/]" if r.passed else "[red]FAIL[/]"
        console.print(f"{mark} {e(r.rule)}: {e(r.message)}", highlight=False)
    render_diagnostics(report.diagnostics, console)
    console.print(f"decision: [{_STATUS_STYLE[report.status]}]{report.status}[/]")
