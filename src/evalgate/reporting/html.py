"""Standalone HTML report: one file, inline CSS, no scripts, no network."""

from collections.abc import Iterable, Sequence
from html import escape

from evalgate.models import CaseDelta, CheckChange, Report

_CSS = """
:root { --bg:#fff; --fg:#1b1f24; --muted:#59636e; --line:#d8dee4; --head:#f6f8fa;
  --pass:#1a7f37; --fail:#cf222e; --invalid:#9a6700; --better:#1a7f37; --worse:#cf222e; }
@media (prefers-color-scheme: dark) { :root { --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1;
  --line:#30363d; --head:#161b22; --pass:#3fb950; --fail:#f85149; --invalid:#d29922;
  --better:#3fb950; --worse:#f85149; } }
body { background:var(--bg); color:var(--fg); margin:0 auto; max-width:1100px; padding:24px 16px;
  font:14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size:20px; margin:0 0 4px; } h2 { font-size:16px; margin:28px 0 8px; }
.muted { color:var(--muted); } .better { color:var(--better); } .worse { color:var(--worse); }
.status { display:inline-block; padding:2px 10px; border-radius:6px; color:#fff; font-weight:600; }
.PASS { background:var(--pass); } .FAIL { background:var(--fail); }
.INVALID { background:var(--invalid); }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; }
th, td { border-bottom:1px solid var(--line); padding:6px 10px; text-align:left;
  vertical-align:top; }
th { background:var(--head); font-weight:600; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
pre { background:var(--head); padding:12px; border-radius:6px; overflow-x:auto; }
"""


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
        f'<p><span class="status {report.status}">{report.status}</span> '
        f'<span class="muted">evalgate {escape(report.evalgate_version)}</span></p>'
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
                            f'<span class="{"worse" if d.level == "error" else "muted"}">'
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
                        ["category", "metric", "cases", "baseline", "candidate", "delta"],
                        (
                            [
                                escape(g.category),
                                escape(g.metric),
                                str(g.cases),
                                _num(g.baseline_mean),
                                _num(g.candidate_mean),
                                _num(g.delta_abs, sign=True),
                            ]
                            for g in cmp.categories
                        ),
                        {2, 3, 4, 5},
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
