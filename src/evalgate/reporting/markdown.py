"""GitHub-flavored Markdown summary: for pull request comments and CI job summaries."""

import re
import unicodedata

from evalgate.models import Report

_TOP = 10  # items per collapsed list
_MAX_RULES = 50  # table rows; failures first, so they are always shown
_MAX_DIAGNOSTICS = 20
_MAX_TEXT = 300  # characters per input-derived value (a message may list every case)
_MAX_TOTAL = 60_000  # GitHub rejects comments over 65,536 characters
_STATUS = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "INVALID": "⚠️ INVALID"}


def _c(text: str, *, cell: bool = False) -> str:
    """Input-derived text as a code span: GitHub then renders it literally, with no HTML,
    formatting, links, @mentions, #references or math."""
    # One line (a newline would end a table row), no invisible or bidi-control characters.
    s = "".join(ch for ch in " ".join(str(text).split()) if unicodedata.category(ch) != "Cf")
    if len(s) > _MAX_TEXT:
        s = s[:_MAX_TEXT] + "…"
    if not s:
        return "(empty)"
    fence = "`" * (max((len(r) for r in re.findall(r"`+", s)), default=0) + 1)
    pad = " " if s[0] == "`" or s[-1] == "`" else ""
    span = f"{fence}{pad}{s}{pad}{fence}"
    return span.replace("|", "\\|") if cell else span  # a table splits cells on any `|`


def _n(x: float, sign: bool = False) -> str:
    x += 0.0  # -0.0 -> 0.0
    return f"{x:+.6g}" if sign else f"{x:.6g}"


def render_markdown(report: Report) -> str:
    out = [f"## EvalGate: {_STATUS[report.status]}", ""]
    b, c = report.baseline, report.candidate
    if b and c:
        out += [
            f"{_c(b.run_id)} → {_c(c.run_id)} · dataset "
            f"{_c(f'{c.dataset_id}@{c.dataset_version}')} · evaluator {_c(c.evaluator_version)}",
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
        rules = failed + [r for r in report.rules if r.passed]  # report order within each
        out += [f"### Rules ({len(failed)} failed of {len(report.rules)})", ""]
        out += ["| | Rule | Result |", "|---|---|---|"]
        for r in rules[: max(_MAX_RULES, len(failed))]:
            mark = "❌" if not r.passed else "✅"
            out.append(f"| {mark} | {_c(r.rule, cell=True)} | {_c(r.message, cell=True)} |")
        if len(rules) > max(_MAX_RULES, len(failed)):
            out += ["", f"{len(rules) - max(_MAX_RULES, len(failed))} passed rules not shown."]
        out.append("")

    if report.diagnostics:
        out += ["### Diagnostics", ""]
        shown = report.diagnostics[:_MAX_DIAGNOSTICS]
        out += [f"- **{d.level}** {_c(d.code)}: {_c(d.message)}" for d in shown]
        if len(report.diagnostics) > len(shown):
            out.append(f"- … {len(report.diagnostics) - len(shown)} more")
        out.append("")

    if cmp and cmp.metrics:
        confidence = f"{cmp.metrics[0].ci.confidence * 100:g}%"  # one policy-wide level
        out += [
            "### Metrics",
            "",
            f"| Metric | Goal | Baseline | Candidate | Δ | Δ % | {confidence} CI of Δ |",
            "|---|---|--:|--:|--:|--:|--:|",
        ]
        for m in cmp.metrics:
            pct = "n/a" if m.delta_pct is None else f"{m.delta_pct + 0.0:+.2f}%"
            out.append(
                f"| {_c(m.name, cell=True)} | {m.direction[:3]} | {_n(m.baseline_mean)} | "
                f"{_n(m.candidate_mean)} | {_n(m.delta_abs, True)} | {pct} | "
                f"[{_n(m.ci.low, True)}, {_n(m.ci.high, True)}] |"
            )
        out.append("")

    if cmp:
        out += _details(
            "Regressed cases",
            [
                f"{_c(d.case_id)} {_c(d.metric)}: {_n(d.baseline)} → {_n(d.candidate)}"
                for d in cmp.regressed_cases
            ],
        )
        out += _details(
            "Newly failing checks",
            [f"{_c(ch.case_id)}: {_c(ch.check)}" for ch in cmp.newly_failing_checks],
        )
    out.append(f"<sub>evalgate {report.evalgate_version}</sub>")
    text = "\n".join(out) + "\n"
    if len(text) > _MAX_TOTAL:  # only with very many failed rules
        cut = text.rfind("\n", 0, _MAX_TOTAL)
        text = text[:cut] + "\n\n… truncated; see the JSON or HTML report for everything.\n"
    return text


def _details(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    shown = f"{len(items)}" + (f", first {_TOP}" if len(items) > _TOP else "")
    lines = [f"<details><summary>{title} ({shown})</summary>", ""]
    lines += [f"- {item}" for item in items[:_TOP]]
    return [*lines, "", "</details>", ""]
