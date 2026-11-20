"""`evalgate history report`: a static history page with server-drawn SVG trend charts.

Same CSS, script and Content Security Policy as the comparison report (html.page). Every chart
and table covers the same selected entries (select_entries); see docs/design/phase-7.md §5.
"""

from collections.abc import Sequence
from html import escape
from typing import Literal

from evalgate.comparison.history import (
    entry_paths,
    entry_value,
    recurring_failures,
    select_entries,
)
from evalgate.models.history import HistoryEntry, RunEntry
from evalgate.reporting.html import Cell, _n, _section, _table, page

WIDTH, HEIGHT, PAD = 640, 200, 40
LEFT = 80  # room for the y labels


def _f(v: float) -> str:
    return f"{v:.2f}"


def svg_chart(path: str, points: Sequence[tuple[int, str, float | None]]) -> str:
    """One metric across entries: (entry id, label, value or None). x is the entry's position
    (evenly spaced, not time); y spans this chart's min..max; a missing value is a gap."""
    values = [v for _, _, v in points if v is not None]
    head = (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="{escape(path)} across {len(points)} entries">'
    )
    if not values:
        return (
            head + f'<text x="{WIDTH // 2}" y="{HEIGHT // 2}" text-anchor="middle">'
            "no values for this path</text></svg>"
        )
    low, high = min(values), max(values)
    n = len(points)

    def x(i: int) -> float:
        return (LEFT + WIDTH - PAD) / 2 if n == 1 else LEFT + i * (WIDTH - LEFT - PAD) / (n - 1)

    def y(v: float) -> float:
        if high == low:
            return HEIGHT / 2
        return HEIGHT - PAD - (v - low) / (high - low) * (HEIGHT - 2 * PAD)

    parts = [head]
    run: list[str] = []
    for i, (_, _, v) in enumerate([*points, (0, "", None)]):  # a sentinel closes the last run
        if v is not None:
            run.append(f"{_f(x(i))},{_f(y(v))}")
            continue
        if len(run) > 1:
            parts.append(f'<polyline points="{" ".join(run)}"/>')
        run = []
    for i, (entry_id, label, v) in enumerate(points):
        if v is not None:
            title = f"#{entry_id}" + (f" {label}" if label else "") + f": {v!r}"
            parts.append(
                f'<circle cx="{_f(x(i))}" cy="{_f(y(v))}" r="3">'
                f"<title>{escape(title)}</title></circle>"
            )
    parts.append(
        f'<text x="{LEFT - 8}" y="{PAD + 4}" text-anchor="end">{escape(f"{high:.6g}")}</text>'
        f'<text x="{LEFT - 8}" y="{HEIGHT - PAD + 4}" text-anchor="end">'
        f"{escape(f'{low:.6g}')}</text>"
        f'<text x="{_f(x(0))}" y="{HEIGHT - 8}" text-anchor="middle">#{points[0][0]}</text>'
    )
    if n > 1:
        parts.append(
            f'<text x="{_f(x(n - 1))}" y="{HEIGHT - 8}" text-anchor="middle">'
            f"#{points[-1][0]}</text>"
        )
    return "".join(parts) + "</svg>"


def render_history_html(
    entries: list[HistoryEntry],
    *,
    kind: Literal["run", "compare"] = "run",
    dataset: str | None = None,
    application: str | None = None,
    last: int | None = None,
    paths: Sequence[str] = (),
) -> str:
    selected = select_entries(
        entries, kind=kind, dataset=dataset, application=application, last=last
    )
    filters = [f"{kind} entries"]
    filters += [f"dataset {dataset}"] if dataset else []
    filters += [f"application {application}"] if application else []
    filters += [f"last {last}"] if last is not None else []
    body = [
        "<h1>EvalGate history</h1>",
        f'<p class="muted">{escape(", ".join(filters))}: {len(selected)} entr'
        f"{'y' if len(selected) == 1 else 'ies'}. INVALID compares are left out.</p>",
    ]
    if not selected:
        body.append("<p>No entries match this selection.</p>")
        return page("EvalGate history", "".join(body))

    def identity(e: HistoryEntry) -> tuple[str, str, str]:
        i = e.run if isinstance(e, RunEntry) else e.candidate
        assert i is not None
        return i.dataset_id, i.dataset_version, i.application_version or ""

    if len({identity(e)[:2] for e in selected}) > 1:
        body.append(
            '<p class="warn">These entries come from more than one dataset: their means '
            "aren't comparable. Filter with --dataset.</p>"
        )
    charts = []
    for path in paths or entry_paths(selected):
        points = [(e.id, e.label or "", entry_value(e, path)) for e in selected]
        charts.append(
            f"<figure><figcaption>{escape(path)}</figcaption>{svg_chart(path, points)}</figure>"
        )
    body.append(_section("Trends", "".join(charts)))

    rows: list[list[Cell]] = []
    for e in selected:
        ds, version, app_version = identity(e)
        status = "valid" if isinstance(e, RunEntry) else e.status
        rows.append(
            [
                _n(e.id),
                escape(e.label or ""),
                status,
                escape(app_version),
                escape(f"{ds}@{version}"),
                e.recorded_at,
            ]
        )
    body.append(
        _section(
            "Entries",
            _table(
                ["id", "label", "status", "version", "dataset", "recorded"],
                rows,
                {0},
                filter=True,
            ),
        )
    )
    failures = recurring_failures(
        entries, kind=kind, dataset=dataset, application=application, last=last
    )
    pairs = failures["pairs"]
    body.append(
        _section(
            "Recurring failures",
            _table(
                ["case", "check", "failing", "of", "first", "last", "streak"],
                (
                    [
                        escape(p["case_id"]),
                        escape(p["check"]),
                        _n(p["failing"]),
                        "n/a" if p["of"] is None else _n(p["of"]),
                        _n(p["first"]),
                        _n(p["last"]),
                        _n(p["streak"]),
                    ]
                    for p in pairs
                ),
                {2, 3, 4, 5, 6},
                filter=True,
            )
            if pairs
            else "<p>No check failed in two or more of these entries.</p>",
        )
    )
    return page("EvalGate history", "".join(body))
