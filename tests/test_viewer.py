# ruff: noqa: E501  (embedded JavaScript and long paths in subprocess code)
"""The interactive HTML report: CSP-pinned script, no-JS completeness, escaping (0.13 T1)."""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from conftest import MANIFEST

import evalgate
from evalgate.engine import evaluate
from evalgate.models import Policy, Report
from evalgate.models.run import CaseResult, Run, RunManifest
from evalgate.reporting.html import render_html

QS = Path(__file__).parent.parent / "examples" / "quickstart"
VIEWER = Path(evalgate.__file__).parent / "reporting" / "viewer.js"
GOLDEN = Path(__file__).parent / "fixtures" / "quickstart_report.html"
HOSTILE = "</script><img src=x onerror=alert(1)>\"'&"


class Markup(HTMLParser):
    """Records real tags and attributes, so escaped text never counts as markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self.rows: dict[int, int] = {}
        self._in: str | None = None
        self._table = -1
        self._body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag in ("script", "style"):
            self._in = tag
        if tag == "table":
            self._table += 1
            self.rows[self._table] = 0
        if tag == "tbody":
            self._body = True
        if tag == "tr" and self._body:
            self.rows[self._table] += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._in = None
        if tag == "tbody":
            self._body = False

    def handle_data(self, data: str) -> None:
        if self._in == "script":
            self.scripts.append(data)
        elif self._in == "style":
            self.styles.append(data)


def parse(html: str) -> Markup:
    m = Markup()
    m.feed(html)
    return m


def quickstart() -> Report:
    return evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml")


def run(*cases: dict[str, Any], **manifest: Any) -> Run:
    return Run(
        manifest=RunManifest(**(MANIFEST | manifest)),
        cases=[CaseResult(**({"status": "ok"} | c)) for c in cases],
    )


POLICY = Policy.model_validate(
    {
        "version": 1,
        "comparison": {"min_compared_cases": 1},
        "metrics": {
            "q": {"path": "scores.q", "direction": "maximize", "max_mean_regression_abs": 1}
        },
        "checks": {"max_new_failures": 99},
        "max_execution_errors": 9,
    }
)


def hostile_report() -> Report:
    base = run(
        {
            "case_id": HOSTILE,
            "scores": {"q": 1},
            "checks": {HOSTILE: True},
            "metadata": {"category": HOSTILE},
        },
        {"case_id": "e", "status": "error", "error": "x"},
        {"case_id": "f", "scores": {"q": 1}},
        run_id=HOSTILE,
    )
    cand = run(
        {
            "case_id": HOSTILE,
            "scores": {"q": 0.5},
            "checks": {HOSTILE: False},
            "metadata": {"category": HOSTILE},
        },
        {"case_id": "e", "scores": {"q": 1}},
        {"case_id": "f", "status": "error", "error": "x"},
        run_id="https://example.com/run",
    )
    return evaluate(base, cand, POLICY)


# --- Content Security Policy -----------------------------------------------------------------


def test_csp_comes_first_and_pins_the_one_script() -> None:
    html = render_html(quickstart())
    m = parse(html)
    assert [t for t, _ in m.tags[:2]] == ["html", "head"]
    assert m.tags[3][0] == "meta" and m.tags[3][1]["http-equiv"] == "Content-Security-Policy"
    assert m.tags[2] == ("meta", {"charset": "utf-8"})  # CSP right after charset
    csp = m.tags[3][1]["content"] or ""
    assert "default-src 'none'" in csp and "base-uri 'none'" in csp and "form-action 'none'" in csp
    [script] = m.scripts
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in csp  # hashed here, not with the renderer's helper
    assert sum(tag == "script" for tag, _ in m.tags) == 1
    assert script == VIEWER.read_text(encoding="utf-8") and script.isascii()


@pytest.mark.parametrize("report", [quickstart, hostile_report], ids=["quickstart", "hostile"])
def test_no_handlers_and_no_external_references(report: Any) -> None:
    m = parse(render_html(report()))
    for tag, attrs in m.tags:
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)
        for name in ("src", "href", "action", "srcset"):
            if name in attrs:
                assert (attrs[name] or "").startswith("data:"), (tag, attrs)
        style = attrs.get("style") or ""
        assert "url(" not in style and "@import" not in style, (tag, attrs)
    outside_style = re.sub(r"<style>.*?</style>", "", render_html(report()), flags=re.S)
    assert "url(" not in "".join(m.scripts) and "@import" not in outside_style


def test_script_source_uses_no_dangerous_api() -> None:
    js = VIEWER.read_text(encoding="utf-8")
    # The browser hashes the bytes between the tags: these would end or comment the block early.
    assert "</script" not in js.lower() and "<!--" not in js
    for token in (
        "fetch", "XMLHttpRequest", "import(", "eval", "Function(", "innerHTML",
        "outerHTML", "insertAdjacentHTML", "document.write",
    ):  # fmt: skip
        assert token not in js, token


# --- complete without JavaScript --------------------------------------------------------------


def sections(html: str) -> dict[str, list[list[list[str]]]]:
    """Section title -> its tables -> body rows -> cell text (unescaped)."""
    out: dict[str, list[list[list[str]]]] = {}
    for chunk in html.split("<details open><summary><h2>")[1:]:
        title, rest = chunk.split("</h2></summary>", 1)
        body = rest.split("</details>", 1)[0]
        tables = re.findall(r"<tbody>(.*?)</tbody>", body, flags=re.S)
        out[unescape(title)] = [
            [
                [
                    unescape(re.sub(r"<[^>]+>", "", c))
                    for c in re.findall(r"<td[^>]*>(.*?)</td>", row)
                ]
                for row in re.findall(r"<tr>(.*?)</tr>", tbody)
            ]
            for tbody in tables
        ]
    return out


def test_new_sections_match_the_json() -> None:
    report = hostile_report()
    cmp = report.comparison
    assert cmp is not None and cmp.failure_groups is not None
    s = sections(render_html(report))
    assert s["Baseline execution errors"] == [[[i] for i in cmp.baseline_errors]] == [[["e"]]]
    assert s["Candidate execution errors"] == [[[i] for i in cmp.candidate_errors]] == [[["f"]]]
    assert s["Candidate failing checks"] == [
        [[c.case_id, c.check] for c in cmp.candidate_failing_checks]
    ]
    g = cmp.failure_groups

    def counts(x: Any) -> list[str]:
        return [str(x.failing), str(x.new), str(x.persistent), str(x.no_baseline)]

    assert s["Failure groups"] == [
        [[x.check, x.category, *counts(x), ", ".join(x.cases)] for x in g.by_check_category],
        [[x.check, *counts(x), ", ".join(x.cases)] for x in g.by_check],
        [[x.category, *counts(x), ", ".join(x.checks), ", ".join(x.cases)] for x in g.by_category],
    ]
    html = render_html(report)
    assert html.count("<details open>") == html.count("<summary>")


def test_failure_groups_absent_for_invalid_or_nothing_failing() -> None:
    ok = evaluate(
        run({"case_id": "a", "scores": {"q": 1}}), run({"case_id": "a", "scores": {"q": 1}}), POLICY
    )
    assert "Failure groups" not in render_html(ok)
    pct = POLICY.model_copy(
        update={
            "metrics": {
                "q": POLICY.metrics["q"].model_copy(
                    update={"max_mean_regression_abs": None, "max_mean_regression_pct": 5}
                )
            }
        }
    )
    base = run({"case_id": "a", "scores": {"q": 0}, "checks": {"s": True}})
    cand = run({"case_id": "a", "scores": {"q": 0}, "checks": {"s": False}})
    invalid = evaluate(base, cand, pct)
    assert invalid.status == "INVALID" and invalid.comparison is not None
    assert "Failure groups" not in render_html(invalid)


def test_numeric_cells_sort_by_full_precision() -> None:
    base = run({"case_id": "a", "scores": {"q": 0.123456789}})
    cand = run({"case_id": "a", "scores": {"q": 0.987654321}})
    html = render_html(evaluate(base, cand, POLICY))
    assert 'data-sort="0.123456789"' in html and 'data-sort="0.987654321"' in html
    assert ">0.123457<" in html  # shown with 6 significant digits


def test_hostile_names_are_escaped() -> None:
    html = render_html(hostile_report())
    assert HOSTILE not in html
    assert "&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;&quot;&#x27;&amp;" in html
    assert "https://example.com/run" in html  # URL-looking user text is just text


# --- determinism -----------------------------------------------------------------------------


def test_matches_the_golden_file() -> None:
    expected = GOLDEN.read_text(encoding="utf-8")
    actual = render_html(quickstart()).replace(
        f"evalgate {evalgate.__version__}", "evalgate VERSION"
    )
    assert actual == expected, "regenerate tests/fixtures/quickstart_report.html if intended"


def test_same_bytes_across_processes() -> None:
    code = (
        "import evalgate,sys; from evalgate.reporting.html import render_html; "
        f"r=evalgate.compare(r'{QS / 'baseline'}', r'{QS / 'candidate'}', r'{QS / 'evalgate.yaml'}'); "
        "sys.stdout.buffer.write(render_html(r).encode())"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env=os.environ | {"PYTHONHASHSEED": seed},
            capture_output=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert len(outs) == 1


# --- the comparator, in Node ------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_comparator_in_node() -> None:
    script = f"""
const vm = require("vm"), fs = require("fs");
const ctx = {{module: {{exports: {{}}}}}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(VIEWER))}, "utf8"), ctx);
const {{cellKey, compareKeys}} = ctx.module.exports;
const cell = (sort, text) => ({{getAttribute: n => sort, textContent: text}});
const keys = [cell("10", "10"), cell("2", "2"), cell(null, "n/a"), cell(null, ""), cell("-1.5", "-1.5")].map(cellKey);
const order = dir => keys.map((k, i) => [k, i]).sort((a, b) => compareKeys(a[0], b[0], dir) || a[1] - b[1]).map(x => x[1]);
const text = [cell(null, "b"), cell(null, "B"), cell(null, "a")].map(cellKey);
const tOrder = text.map((k, i) => [k, i]).sort((a, b) => compareKeys(a[0], b[0], 1) || a[1] - b[1]).map(x => x[1]);
const tie = compareKeys(cellKey(cell("3", "3")), cellKey(cell("3", "3")), 1);
process.stdout.write(JSON.stringify({{asc: order(1), desc: order(-1), text: tOrder, tie}}));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    result = json.loads(out)
    assert result["asc"] == [4, 1, 0, 2, 3]  # -1.5, 2, 10, then n/a and empty last
    assert result["desc"] == [0, 1, 4, 2, 3]  # 10, 2, -1.5, empties still last
    assert result["text"] == [1, 2, 0]  # "B" < "a" < "b": code units, not locale
    assert result["tie"] == 0
