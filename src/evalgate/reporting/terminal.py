"""Compact terminal summary for local use and CI logs."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape as e  # every input-derived string goes through this
from rich.table import Table

from evalgate.models import Diagnostic, Report
from evalgate.models.calibration import CalibrationReport
from evalgate.models.multi import MultiSummary

if TYPE_CHECKING:
    from evalgate.api import MultiResult as MultiResultLike
from evalgate.models.history import HistoryEntry, Identity, RunEntry

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
        rep = cmp.repeats
        if rep:
            console.print(
                f"repeats per case: baseline {rep.baseline_repeats}, "
                f"candidate {rep.candidate_repeats} (cases are the unit of analysis)",
                highlight=False,
            )
            for rm in rep.metrics:
                sds = [
                    f"{s:.4g}" if s is not None else "n/a"
                    for s in (rm.baseline_sd, rm.candidate_sd)
                ]
                console.print(
                    f"  noise {e(rm.name)}: within-case SD {sds[0]} -> {sds[1]}", highlight=False
                )
            for rc in rep.checks:
                if min(rc.baseline_pass_rate, rc.candidate_pass_rate) < 1:
                    console.print(
                        f"  pass rate {e(rc.check)}: {rc.baseline_pass_rate:.1%} -> "
                        f"{rc.candidate_pass_rate:.1%}",
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


SMALL_CALIBRATION = 30  # advisory, like SMALL_SAMPLE: few cases make agreement figures noisy


def _figure(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_calibration(report: CalibrationReport, console: Console) -> None:
    """One row per labelled (kind, name). Reporting only: there is no decision."""
    console.print(
        f"calibration of [bold]{e(report.run_id)}[/] against "
        f"{report.labelled_cases} labelled case(s)",
        highlight=False,
    )
    table = Table()
    table.add_column("kind")
    table.add_column("name", overflow="fold")  # the column that matters: never truncate it
    # Short headers so the table fits 80-column CI logs.
    for column in ("n", "cases", "err", "miss", "acc", "kappa", "mae", "rho"):
        table.add_column(column, justify="right")
    for x in report.entries:
        table.add_row(
            x.kind,
            e(x.name),
            str(x.n),
            str(x.n_cases),
            str(x.errors),
            str(x.missing),
            *(_figure(v) for v in (x.accuracy, x.kappa, x.mae, x.spearman)),
        )
    console.print(table)
    if small := [x for x in report.entries if x.n_cases < SMALL_CALIBRATION]:
        names = ", ".join(f"{x.kind} {x.name}" for x in small)
        console.print(
            f"[yellow]warning[/] fewer than {SMALL_CALIBRATION} labelled cases for "
            f"{e(names)}: agreement figures are noisy",
            highlight=False,
        )


def _who(identity: Identity | None) -> str:
    if identity is None:
        return "-"
    version = identity.application_version or ""
    return f"{identity.application or identity.run_id} {version}".strip()


def _text(value: object) -> str:
    # Labels and ids are user text: escape markup and show control characters literally.
    return e(repr(value)[1:-1]) if isinstance(value, str) else e(str(value))


def render_history_list(entries: list[HistoryEntry], console: Console) -> None:
    table = Table()
    for column in ("id", "kind", "label", "status", "candidate / run", "dataset", "recorded"):
        table.add_column(column, overflow="fold", justify="right" if column == "id" else "left")
    for x in entries:
        identity = x.run if isinstance(x, RunEntry) else x.candidate
        table.add_row(
            str(x.id),
            x.kind,
            _text(x.label or ""),
            "valid" if isinstance(x, RunEntry) else x.status,
            _text(_who(identity)),
            _text(f"{identity.dataset_id}@{identity.dataset_version}" if identity else "-"),
            x.recorded_at,
        )
    console.print(table)


def render_run_entry(entry: RunEntry, console: Console) -> None:
    r = entry.run
    console.print(
        f"run [bold]{_text(r.run_id)}[/]  {_text(_who(r))}  "
        f"dataset {_text(r.dataset_id)}@{_text(r.dataset_version)}  "
        f"label {_text(entry.label or '-')}",
        highlight=False,
    )
    console.print(f"cases: {entry.cases}, errors: {entry.errors}", highlight=False)
    table = Table()
    for column in ("path", "mean / pass rate", "cases"):
        table.add_column(column, overflow="fold", justify="left" if column == "path" else "right")
    for path, stat in entry.means.items():
        table.add_row(_text(path), f"{stat.value:.4g}", str(stat.cases))
    for name, stat in entry.checks.items():
        table.add_row(_text(f"checks.{name}"), f"{stat.value:.1%}", str(stat.cases))
    console.print(table)


def render_trend(trend: dict[str, object], console: Console) -> None:
    points = trend["points"]
    assert isinstance(points, list)
    console.print(
        f"trend of [bold]{_text(trend['path'])}[/] over {trend['kind']} entries "
        f"({len(points)} point(s), {trend['skipped']} skipped without it)",
        highlight=False,
    )
    table = Table()
    for column in ("id", "label", "version", "dataset", "value", "cases"):
        table.add_column(
            column,
            overflow="fold",
            justify="right" if column in ("id", "value", "cases") else "left",
        )
    for p in points:
        table.add_row(
            str(p["id"]),
            _text(p["label"] or ""),
            _text(p["application_version"] or ""),
            _text(p["dataset"]),
            f"{p['value']:.4g}",
            str(p["cases"]),
        )
    console.print(table)
    if trend["mixed_datasets"]:
        console.print(
            "[yellow]warning[/] points come from more than one dataset: their means aren't "
            "comparable; filter with --dataset",
            highlight=False,
        )


def render_candidates(result: "MultiResultLike", paths: Sequence[Path], console: Console) -> None:
    """The closing table of a multi-candidate compare. The ranking is informational only."""
    summary: MultiSummary = result.summary()
    ranks = {i.n: i for i in summary.ranking or []}
    table = Table(
        title="candidates" + (f" ranked by {e(summary.rank_by)}" if summary.rank_by else "")
    )
    columns = ["n", "candidate", "run_id", "status"]
    if summary.ranking is not None:
        columns += ["rank", "value", "cases"]
    for column in columns:
        table.add_column(
            column,
            overflow="fold",
            justify="right" if column in ("n", "rank", "value", "cases") else "left",
        )
    for c, path in zip(summary.candidates, paths, strict=True):
        row = [str(c.n), e(str(path)), _text(c.run_id or "-"), c.status]
        if summary.ranking is not None:
            item = ranks.get(c.n)
            row += (
                [str(item.rank), f"{item.value:.4g}", str(item.cases)] if item else ["-", "-", "-"]
            )
        table.add_row(*row)
    console.print(table)
    if summary.cases_differ:
        console.print(
            "[yellow]warning[/] ranked candidates were compared on different numbers of cases: "
            "their means aren't directly comparable",
            highlight=False,
        )
    console.print(f"exit code: {summary.exit_code}", highlight=False)
