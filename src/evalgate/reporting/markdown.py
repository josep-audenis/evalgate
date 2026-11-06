"""GitHub-flavored Markdown summary: for pull request comments and CI job summaries."""

import html
import re

from evalgate.models import Report

_TOP = 10
_STATUS = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "INVALID": "⚠️ INVALID"}
# Inline formatting, links/images, table cells and headings; `<` and `&` go through html.escape.
_SPECIAL = re.compile(r"([\\`*_\[\]|~#])")


def _t(text: str) -> str:
    """Input-derived text as inert Markdown: no HTML, links, formatting or table breaks."""
    one_line = " ".join(str(text).split())  # newlines would end a table row
    escaped = _SPECIAL.sub(r"\\\1", html.escape(one_line, quote=False))
    # A leading `-`, `+` or `1.` could start a nested list inside a list item.
    return re.sub(r"^([-+])|^(\d+)\.", lambda m: f"\\{m[1]}" if m[1] else f"{m[2]}\\.", escaped)


def _n(x: float, sign: bool = False) -> str:
    return f"{x:+.4g}" if sign else f"{x:.4g}"


def render_markdown(report: Report) -> str:
    out = [f"## EvalGate: {_STATUS[report.status]}", ""]
    b, c = report.baseline, report.candidate
    if b and c:
        out += [
            f"**{_t(b.run_id)}** → **{_t(c.run_id)}** · dataset "
            f"{_t(c.dataset_id)}@{_t(c.dataset_version)} · evaluator {_t(c.evaluator_version)}",
            "",
        ]
    cmp = report.comparison
    if cmp:
        line = (
            f"{cmp.compared_cases} cases compared ({cmp.matched_cases} matched, "
            f"{len(cmp.candidate_errors)} candidate errors)"
        )
        if cmp.repeats:
            rep = cmp.repeats
            line += f" · repeats per case {rep.baseline_repeats} → {rep.candidate_repeats}"
        out += [line, ""]

    failed = [r for r in report.rules if not r.passed]
    if report.rules:
        out += [f"### Rules ({len(failed)} failed of {len(report.rules)})", ""]
        out += ["| | Rule | Result |", "|---|---|---|"]
        # Failures first, each group in the report's (deterministic) order.
        for r in failed + [r for r in report.rules if r.passed]:
            out.append(f"| {'❌' if not r.passed else '✅'} | {_t(r.rule)} | {_t(r.message)} |")
        out.append("")

    if report.diagnostics:
        out += ["### Diagnostics", ""]
        out += [f"- **{d.level}** {_t(d.code)}: {_t(d.message)}" for d in report.diagnostics]
        out.append("")

    if cmp and cmp.metrics:
        ci = cmp.metrics[0].ci
        out += [
            "### Metrics",
            "",
            f"| Metric | Goal | Baseline | Candidate | Δ | Δ % | {ci.confidence:.0%} CI of Δ |",
            "|---|---|--:|--:|--:|--:|--:|",
        ]
        for m in cmp.metrics:
            pct = "n/a" if m.delta_pct is None else f"{m.delta_pct:+.2f}%"
            out.append(
                f"| {_t(m.name)} | {m.direction[:3]} | {_n(m.baseline_mean)} | "
                f"{_n(m.candidate_mean)} | {_n(m.delta_abs, True)} | {pct} | "
                f"[{_n(m.ci.low, True)}, {_n(m.ci.high, True)}] |"
            )
        out.append("")

    if cmp:
        out += _details(
            "Regressed cases",
            [
                f"{_t(d.case_id)} · {_t(d.metric)}: {d.baseline:.6g} → {d.candidate:.6g}"
                for d in cmp.regressed_cases
            ],
        )
        out += _details(
            "Newly failing checks",
            [f"{_t(ch.case_id)}: {_t(ch.check)}" for ch in cmp.newly_failing_checks],
        )
    out.append(f"<sub>evalgate {_t(report.evalgate_version)}</sub>")
    return "\n".join(out) + "\n"


def _details(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    shown = f"{len(items)}" + (f", first {_TOP}" if len(items) > _TOP else "")
    lines = [f"<details><summary>{title} ({shown})</summary>", ""]
    lines += [f"- {item}" for item in items[:_TOP]]
    return [*lines, "", "</details>", ""]
