"""Standalone HTML report: one file, inline CSS, one CSP-pinned script, no network.

The page is complete without JavaScript; the script (viewer.js) only sorts, filters and moves
rows that are already there. A Content Security Policy allows exactly that script and nothing
else to load or run (docs/design/phase-7.md section 5).
"""

import base64
import hashlib
from collections.abc import Iterable, Sequence
from functools import cache
from html import escape
from importlib.resources import files

from evalgate.models import CaseDelta, CheckChange, Report
from evalgate.models.report import CategoryGroup, CheckCategoryGroup, CheckGroup, FailureGroups

# EvalGate design system tokens (night default, paper when the OS is light). No web font:
# the report stays offline, so Martian Mono renders only where it is installed.
_CSS = """
:root { --bg:#0c0d0c; --bg-2:#121412; --fg:#d9d6cd; --dim:#96938a; --faint:#2c2d2a;
  --sel:#23251f; --red:#ff6b57; --green:#8fd694; --yellow:#e8c547;
  --mono:"Martian Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; }
@media (prefers-color-scheme: light) { :root { --bg:#e8e8e3; --bg-2:#dfdfd9; --fg:#191a17;
  --dim:#5d5c55; --faint:#c3c3bb; --sel:#d4d4cc; --red:#922512; --green:#2a6e35;
  --yellow:#6e5300; } }
body { background:var(--bg); color:var(--fg); margin:0 auto; max-width:1240px;
  padding:24px clamp(16px, 4vw, 56px) 56px; font:400 13px/1.65 var(--mono); font-stretch:87.5%;
  -webkit-font-smoothing:antialiased; }
::selection { background:var(--fg); color:var(--bg); }
h1 { font-size:15px; font-weight:700; margin:0 0 18px; }
h2 { font-size:15px; font-weight:700; letter-spacing:.02em; text-transform:uppercase;
  margin:56px 0 18px; }
p, ul { max-width:92ch; }
.muted { color:var(--dim); } .better { color:var(--green); } .worse { color:var(--red); }
.warn { color:var(--yellow); }
.decision { font-size:clamp(28px, 6vw, 56px); font-weight:760; font-stretch:75%; line-height:1;
  letter-spacing:-.03em; margin:0 0 8px; }
.decision .k { font-weight:400; color:var(--dim); }
.status { font-weight:760; } .PASS { color:var(--green); } .FAIL { color:var(--red); }
.INVALID { color:var(--yellow); }
.scroll { overflow-x:auto; border:1px solid var(--faint); background:var(--bg-2); }
table { border-collapse:collapse; width:100%; }
th, td { border-bottom:1px solid var(--faint); padding:6px 14px; text-align:left;
  vertical-align:top; }
tr:last-child td { border-bottom:0; }
th { color:var(--dim); font-weight:400; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.scroll + .scroll { margin-top:18px; }
pre { font:inherit; line-height:1.75; background:var(--bg-2); border:1px solid var(--faint);
  padding:12px 16px; overflow-x:auto; }
details > summary { cursor:pointer; list-style-position:outside; }
details > summary h2 { display:inline; }
th button { font:inherit; color:inherit; background:none; border:0; padding:0; cursor:pointer;
  text-align:inherit; }
th[aria-sort="ascending"] button::after { content:" \\2191" / ""; }
th[aria-sort="descending"] button::after { content:" \\2193" / ""; }
figure { margin:0 0 24px; } figcaption { color:var(--dim); margin:0 0 6px; }
svg.chart { width:100%; max-width:640px; height:auto; display:block; background:var(--bg-2);
  border:1px solid var(--faint); }
svg.chart polyline { fill:none; stroke:var(--green); stroke-width:1.5; }
svg.chart circle { fill:var(--fg); }
svg.chart text { fill:var(--dim); font:11px var(--mono); }
input.filter { font:inherit; color:var(--fg); background:var(--bg-2);
  border:1px solid var(--faint); padding:4px 8px; margin:0 0 8px; }
"""


@cache
def _script() -> tuple[str, str]:
    """The viewer script and its CSP source: 'sha256-' + base64 of the SHA-256 of its text."""
    js = files("evalgate.reporting").joinpath("viewer.js").read_text(encoding="utf-8")
    digest = base64.b64encode(hashlib.sha256(js.encode("utf-8")).digest()).decode("ascii")
    return js, f"'sha256-{digest}'"


def page(title: str, body: str) -> str:
    """A complete document: CSP first, then style, body and the one pinned script."""
    js, source = _script()
    csp = (
        f"default-src 'none'; style-src 'unsafe-inline'; script-src {source}; img-src data:; "
        "base-uri 'none'; form-action 'none'"
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<link rel="icon" href="data:,">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><style>{_CSS}</style></head>"
        f"<body>{body}<script>{js}</script></body></html>\n"
    )


Cell = str | tuple[str, float | None]  # pre-escaped HTML, optionally with a numeric sort key

_EXIT = {"PASS": 0, "FAIL": 1, "INVALID": 2}


def _num(x: float, sign: bool = False) -> str:
    return f"{x:+.6g}" if sign else f"{x:.6g}"


def _n(x: float, sign: bool = False, html: str | None = None) -> Cell:
    """A number shown with 6 significant digits but sorted by its full value."""
    return (_num(x, sign) if html is None else html, x)


def _table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Cell]],
    numeric: set[int],
    *,
    filter: bool = False,
) -> str:
    """Cells are pre-escaped HTML; `numeric` holds right-aligned column indexes. Every table is
    sortable; `filter` adds a filter box (created by the script, so no-JS readers never see it)."""

    def cls(i: int) -> str:
        return ' class="num"' if i in numeric else ""

    def td(i: int, cell: Cell) -> str:
        if isinstance(cell, tuple):
            html, key = cell
            sort = "" if key is None else f' data-sort="{escape(repr(key))}"'
            return f"<td{cls(i)}{sort}>{html}</td>"
        return f"<td{cls(i)}>{cell}</td>"

    head = "".join(f'<th{cls(i)} scope="col">{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(td(i, c) for i, c in enumerate(row)) + "</tr>" for row in rows)
    attrs = " data-sortable" + (" data-filter" if filter else "")
    return (
        f'<div class="scroll"><table{attrs}><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _section(title: str, content: str) -> str:
    # Open by default so find-in-page and printing see everything; folds without script.
    return f"<details open><summary><h2>{escape(title)}</h2></summary>{content}</details>"


def _case_deltas(deltas: list[CaseDelta], css: str) -> str:
    return _table(
        ["case", "metric", "baseline", "candidate", "delta"],
        (
            [
                escape(d.case_id),
                escape(d.metric),
                _n(d.baseline),
                _n(d.candidate),
                _n(d.delta_abs, html=f'<span class="{css}">{_num(d.delta_abs, sign=True)}</span>'),
            ]
            for d in deltas
        ),
        {2, 3, 4},
        filter=True,
    )


def _checks(changes: list[CheckChange]) -> str:
    return _table(
        ["case", "check"],
        ([escape(c.case_id), escape(c.check)] for c in changes),
        set(),
        filter=True,
    )


def render_html(report: Report) -> str:
    parts: list[str] = []
    b, c, cmp = report.baseline, report.candidate, report.comparison

    title = f"{b.run_id} → {c.run_id}" if b and c else "comparison"
    parts.append(
        f"<h1>EvalGate: {escape(title)}</h1>"
        f'<p class="decision"><span class="k">decision: </span>'
        f'<span class="status {report.status}">{report.status}</span></p>'
        f'<p class="muted">exit {_EXIT[report.status]} · '
        f"evalgate {escape(report.evalgate_version)}</p>"
    )

    failed = [r for r in report.rules if not r.passed]
    if failed:
        parts.append(
            _section(
                "Failed policies",
                "<ul>"
                + "".join(f"<li><b>{escape(r.rule)}</b>: {escape(r.message)}</li>" for r in failed)
                + "</ul>",
            )
        )
    if report.diagnostics:
        parts.append(
            _section(
                "Diagnostics",
                _table(
                    ["level", "code", "message"],
                    (
                        [
                            f'<span class="{"worse" if d.level == "error" else "warn"}">'
                            f"{d.level}</span>",
                            escape(d.code),
                            escape(d.message),
                        ]
                        for d in report.diagnostics
                    ),
                    set(),
                ),
            )
        )

    if b and c:
        fields = ["run_id", "application", "application_version", "dataset_id"]
        fields += ["dataset_version", "evaluator_version"]
        parts.append(
            _section(
                "Runs",
                _table(
                    ["", "baseline", "candidate"],
                    (
                        [
                            escape(f),
                            escape(str(getattr(b, f) or "")),
                            escape(str(getattr(c, f) or "")),
                        ]
                        for f in fields
                    ),
                    set(),
                ),
            )
        )

    if cmp:
        parts.append(
            f"<p>{cmp.matched_cases} matched cases, {cmp.compared_cases} compared, "
            f"{len(cmp.baseline_errors)} baseline errors, "
            f"{len(cmp.candidate_errors)} candidate errors.</p>"
        )
        metric_rows: list[list[Cell]] = []
        for m in cmp.metrics:
            worse = m.delta_abs < 0 if m.direction == "maximize" else m.delta_abs > 0
            css = "worse" if worse else "better" if m.delta_abs else "muted"
            pct = "n/a" if m.delta_pct is None else f"{m.delta_pct:+.2f}%"
            metric_rows.append(
                [
                    escape(m.name),
                    escape(m.direction),
                    _n(m.cases),
                    _n(m.baseline_mean),
                    _n(m.candidate_mean),
                    _n(
                        m.delta_abs,
                        html=f'<span class="{css}">{_num(m.delta_abs, sign=True)}</span>',
                    ),
                    (f'<span class="{css}">{pct}</span>', m.delta_pct),
                    _n(
                        m.ci.low,
                        html=f"[{_num(m.ci.low, sign=True)}, {_num(m.ci.high, sign=True)}]",
                    ),
                ]
            )
        if metric_rows:
            ci = cmp.metrics[0].ci
            headers = ["metric", "direction", "cases", "baseline", "candidate", "delta", "%"]
            parts.append(
                _section(
                    "Metrics",
                    _table([*headers, "CI of delta"], metric_rows, {2, 3, 4, 5, 6, 7})
                    + f'<p class="muted">CI: {ci.confidence:.0%} confidence interval of the mean '
                    f"change over {cmp.compared_cases} paired cases ({escape(ci.method)}"
                    + (f", {ci.resamples} resamples" if ci.resamples else "")
                    + "). An interval that spans the threshold means the data cannot tell "
                    "whether the limit is met.</p>",
                )
            )
        if report.rules:
            parts.append(
                _section(
                    "Policy rules",
                    _table(
                        ["", "rule", "detail"],
                        (
                            [
                                '<span class="better">pass</span>'
                                if r.passed
                                else '<span class="worse">FAIL</span>',
                                escape(r.rule),
                                escape(r.message),
                            ]
                            for r in report.rules
                        ),
                        set(),
                    ),
                )
            )
        for name, ids in (
            ("Baseline execution errors", cmp.baseline_errors),
            ("Candidate execution errors", cmp.candidate_errors),
        ):
            if ids:
                parts.append(
                    _section(name, _table(["case"], ([escape(i)] for i in ids), set(), filter=True))
                )
        if cmp.newly_failing_checks:
            parts.append(_section("Newly failing checks", _checks(cmp.newly_failing_checks)))
        if cmp.regressed_cases:
            parts.append(_section("Regressed cases", _case_deltas(cmp.regressed_cases, "worse")))
        if cmp.improved_cases:
            parts.append(_section("Improved cases", _case_deltas(cmp.improved_cases, "better")))
        if cmp.newly_passing_checks:
            parts.append(_section("Newly passing checks", _checks(cmp.newly_passing_checks)))
        if cmp.candidate_failing_checks:
            parts.append(
                _section("Candidate failing checks", _checks(cmp.candidate_failing_checks))
            )
        if report.status != "INVALID" and cmp.failure_groups and cmp.failure_groups.by_check:
            parts.append(_section("Failure groups", _failure_groups(cmp.failure_groups)))
        if cmp.repeats:
            rep = cmp.repeats

            def sd(x: float | None) -> Cell:
                return "n/a" if x is None else _n(x)

            parts.append(
                _section(
                    "Repeats",
                    f"<p>{rep.baseline_repeats} repeat(s) per case in the baseline, "
                    f"{rep.candidate_repeats} in the candidate. Repeats are combined per case "
                    "(metric mean; a check passes only if every repeat passes; any errored repeat "
                    "makes the case an error), so cases remain the unit of analysis.</p>"
                    + _table(
                        ["metric", "baseline within-case SD", "candidate within-case SD"],
                        (
                            [escape(m.name), sd(m.baseline_sd), sd(m.candidate_sd)]
                            for m in rep.metrics
                        ),
                        {1, 2},
                    )
                    + _table(
                        ["check", "baseline pass rate", "candidate pass rate"],
                        (
                            [
                                escape(c.check),
                                (f"{c.baseline_pass_rate:.1%}", c.baseline_pass_rate),
                                (f"{c.candidate_pass_rate:.1%}", c.candidate_pass_rate),
                            ]
                            for c in rep.checks
                        ),
                        {1, 2},
                    ),
                )
            )
        if cmp.check_stats:
            parts.append(
                _section(
                    "Checks",
                    _table(
                        [
                            "check",
                            "cases",
                            "baseline fails",
                            "candidate fails",
                            "newly failing",
                            "newly passing",
                            "p (McNemar)",
                        ],
                        (
                            [
                                escape(s.check),
                                _n(s.cases),
                                _n(s.baseline_failures),
                                _n(s.candidate_failures),
                                (
                                    f'<span class="{"worse" if s.newly_failing else "muted"}">'
                                    f"{s.newly_failing}</span>",
                                    s.newly_failing,
                                ),
                                _n(s.newly_passing),
                                (f"{s.p_value:.3g}", s.p_value),
                            ]
                            for s in cmp.check_stats
                        ),
                        {1, 2, 3, 4, 5, 6},
                    )
                    + '<p class="muted">p: one-sided exact McNemar test that the candidate '
                    "fails the check more often, using only cases that flipped. A large p can "
                    "also mean too few flips to tell.</p>",
                )
            )
        if cmp.categories:
            parts.append(
                _section(
                    "By category",
                    _table(
                        ["category", "metric", "cases", "baseline", "candidate", "delta", "CI"],
                        (
                            [
                                escape(g.category),
                                escape(g.metric),
                                _n(g.cases),
                                _n(g.baseline_mean),
                                _n(g.candidate_mean),
                                _n(g.delta_abs, sign=True),
                                _n(
                                    g.ci.low,
                                    html=f"[{_num(g.ci.low, sign=True)}, "
                                    f"{_num(g.ci.high, sign=True)}]",
                                ),
                            ]
                            for g in cmp.categories
                        ),
                        {2, 3, 4, 5, 6},
                        filter=True,
                    ),
                )
            )

    if report.policy:
        policy_json = report.policy.model_dump_json(indent=2)
        parts.append(_section("Policy", f"<pre>{escape(policy_json)}</pre>"))

    return page(f"EvalGate {report.status}: {escape(title)}", "".join(parts))


def _failure_groups(groups: FailureGroups) -> str:
    counts = ["failing", "new", "persistent", "no baseline"]

    def n(g: CheckGroup | CategoryGroup | CheckCategoryGroup) -> list[Cell]:
        return [_n(g.failing), _n(g.new), _n(g.persistent), _n(g.no_baseline)]

    def cases(ids: list[str]) -> str:
        return escape(", ".join(ids))

    html = (
        '<p class="muted">Failing checks of compared cases. failing = new (passed in the '
        "baseline) + persistent (failed there too) + no baseline (the baseline had no such "
        "check).</p>"
    )
    if groups.by_check_category:
        html += _table(
            ["check", "category", *counts, "cases"],
            (
                [escape(g.check), escape(g.category), *n(g), cases(g.cases)]
                for g in groups.by_check_category
            ),
            {2, 3, 4, 5},
            filter=True,
        )
    html += _table(
        ["check", *counts, "cases"],
        ([escape(g.check), *n(g), cases(g.cases)] for g in groups.by_check),
        {1, 2, 3, 4},
        filter=True,
    )
    if groups.by_category:
        html += _table(
            ["category", *counts, "checks", "cases"],
            (
                [escape(g.category), *n(g), cases(g.checks), cases(g.cases)]
                for g in groups.by_category
            ),
            {1, 2, 3, 4},
            filter=True,
        )
    if groups.uncategorized:
        html += (
            f'<p class="muted">{groups.uncategorized} failing pair(s) without a category '
            "are counted by check only.</p>"
        )
    return html
