"""Standalone HTML report: one file, inline CSS, no scripts, no network."""

from collections.abc import Iterable, Sequence
from html import escape

from evalgate.models import CaseDelta, CheckChange, Report

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
"""

_EXIT = {"PASS": 0, "FAIL": 1, "INVALID": 2}


def _num(x: float, sign: bool = False) -> str:
    return f"{x:+.6g}" if sign else f"{x:.6g}"


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]], numeric: set[int]) -> str:
    """Cells are pre-escaped HTML; `numeric` holds right-aligned column indexes."""

    def cls(i: int) -> str:
        return ' class="num"' if i in numeric else ""

    head = "".join(f"<th{cls(i)}>{escape(h)}</th>" for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>'


def _section(title: str, content: str) -> str:
    return f"<h2>{escape(title)}</h2>{content}"


def _case_deltas(deltas: list[CaseDelta], css: str) -> str:
    return _table(
        ["case", "metric", "baseline", "candidate", "delta"],
        (
            [
                escape(d.case_id),
                escape(d.metric),
                _num(d.baseline),
                _num(d.candidate),
                f'<span class="{css}">{_num(d.delta_abs, sign=True)}</span>',
            ]
            for d in deltas
        ),
        {2, 3, 4},
    )


def _checks(changes: list[CheckChange]) -> str:
    return _table(["case", "check"], ([escape(c.case_id), escape(c.check)] for c in changes), set())


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
        metric_rows = []
        for m in cmp.metrics:
            worse = m.delta_abs < 0 if m.direction == "maximize" else m.delta_abs > 0
            css = "worse" if worse else "better" if m.delta_abs else "muted"
            pct = "n/a" if m.delta_pct is None else f"{m.delta_pct:+.2f}%"
            metric_rows.append(
                [
                    escape(m.name),
                    escape(m.direction),
                    str(m.cases),
                    _num(m.baseline_mean),
                    _num(m.candidate_mean),
                    f'<span class="{css}">{_num(m.delta_abs, sign=True)}</span>',
                    f'<span class="{css}">{pct}</span>',
                    f"[{_num(m.ci.low, sign=True)}, {_num(m.ci.high, sign=True)}]",
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
        if cmp.candidate_errors:
            parts.append(
                _section(
                    "Candidate execution errors", "<p>" + escape(", ".join(cmp.candidate_errors))
                )
            )
        if cmp.newly_failing_checks:
            parts.append(_section("Newly failing checks", _checks(cmp.newly_failing_checks)))
        if cmp.regressed_cases:
            parts.append(_section("Regressed cases", _case_deltas(cmp.regressed_cases, "worse")))
        if cmp.improved_cases:
            parts.append(_section("Improved cases", _case_deltas(cmp.improved_cases, "better")))
        if cmp.newly_passing_checks:
            parts.append(_section("Newly passing checks", _checks(cmp.newly_passing_checks)))
        if cmp.repeats:
            rep = cmp.repeats

            def sd(x: float | None) -> str:
                return "n/a" if x is None else _num(x)

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
                                f"{c.baseline_pass_rate:.1%}",
                                f"{c.candidate_pass_rate:.1%}",
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
                                str(s.cases),
                                str(s.baseline_failures),
                                str(s.candidate_failures),
                                f'<span class="{"worse" if s.newly_failing else "muted"}">'
                                f"{s.newly_failing}</span>",
                                str(s.newly_passing),
                                f"{s.p_value:.3g}",
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
                                str(g.cases),
                                _num(g.baseline_mean),
                                _num(g.candidate_mean),
                                _num(g.delta_abs, sign=True),
                                f"[{_num(g.ci.low, sign=True)}, {_num(g.ci.high, sign=True)}]",
                            ]
                            for g in cmp.categories
                        ),
                        {2, 3, 4, 5, 6},
                    ),
                )
            )

    if report.policy:
        policy_json = report.policy.model_dump_json(indent=2)
        parts.append(_section("Policy", f"<pre>{escape(policy_json)}</pre>"))

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>EvalGate {report.status}: {escape(title)}</title>"
        f"<style>{_CSS}</style></head><body>{''.join(parts)}</body></html>\n"
    )
