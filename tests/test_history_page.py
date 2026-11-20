# ruff: noqa: E501  (long CLI argument lists)
"""`evalgate history report`: static page with SVG trend charts (0.13 T2)."""

import base64
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_viewer import parse
from typer.testing import CliRunner

import evalgate
from evalgate import EvalRun, read_history, record_report, record_run
from evalgate.cli import EXIT_INVALID, app
from evalgate.comparison.history import recurring_failures, select_entries
from evalgate.reporting.history_html import render_history_html, svg_chart

runner = CliRunner()
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
QS = Path(__file__).parent.parent / "examples" / "quickstart"


def circles(svg: str) -> list[tuple[str, str]]:
    return re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)"', svg)


# --- SVG geometry (viewBox 640x200, left 80, padding 40) --------------------------------------


def test_two_points() -> None:
    svg = svg_chart("scores.q", [(1, "a", 1.0), (2, "b", 3.0)])
    assert circles(svg) == [("80.00", "160.00"), ("600.00", "40.00")]
    assert '<polyline points="80.00,160.00 600.00,40.00"/>' in svg
    assert ">#1</text>" in svg and ">#2</text>" in svg


def test_gap_splits_the_line() -> None:
    svg = svg_chart("scores.q", [(1, "", 1.0), (2, "", None), (3, "", 3.0)])
    assert circles(svg) == [("80.00", "160.00"), ("600.00", "40.00")]
    assert "<polyline" not in svg  # each run has one point: circles only


def test_one_point_and_equal_values_sit_mid_height() -> None:
    assert circles(svg_chart("p", [(7, "", 2.5)])) == [("340.00", "100.00")]
    assert circles(svg_chart("p", [(1, "", 2.0), (2, "", 2.0)])) == [
        ("80.00", "100.00"),
        ("600.00", "100.00"),
    ]


def test_x_labels_only_at_the_ends() -> None:
    svg = svg_chart("p", [(i, "", float(i)) for i in range(1, 6)])
    assert re.findall(r">#(\d+)</text>", svg) == ["1", "5"]


def test_no_values() -> None:
    assert "no values for this path" in svg_chart("p", [(1, "", None)])


def test_titles_hold_full_values_and_are_escaped() -> None:
    svg = svg_chart("<p>", [(1, "<b>&", 0.123456789)])
    assert "<title>#1 &lt;b&gt;&amp;: 0.123456789</title>" in svg
    assert 'aria-label="&lt;p&gt; across 1 entries"' in svg


# --- the page ----------------------------------------------------------------------------------


def make_run(path: Path, quality: float, failing: bool, dataset: str = "d") -> Path:
    with EvalRun(
        path.name, dataset=dataset, dataset_version="v", evaluator_version="e", path=path
    ) as r:
        r.record("c1", scores={"quality": quality}, checks={"safe": not failing})
    return path


def store(tmp_path: Path, *runs: tuple[float, bool]) -> Path:
    s = tmp_path / "h"
    for i, (q, failing) in enumerate(runs):
        record_run(make_run(tmp_path / f"r{i}", q, failing), s, recorded_at=T0, label=f"v{i}")
    return s


def test_page_shares_one_selection_and_matches_failures(tmp_path: Path) -> None:
    s = store(tmp_path, (0.5, True), (0.7, True), (0.9, False))
    entries = read_history(s)
    html = render_history_html(entries, last=2)
    assert [e.id for e in select_entries(entries, last=2)] == [2, 3]
    assert "2 entries" in html
    chart = html.split("<figcaption>scores.quality</figcaption>")[1].split("</svg>")[0]
    assert len(circles(chart)) == 2
    failures = recurring_failures(entries, last=2)
    assert failures["pairs"] == []  # failing once in the window
    assert "No check failed in two or more" in html
    full = render_history_html(entries)
    assert "<td>c1</td><td>safe</td>" in full


def test_path_option_and_missing_values(tmp_path: Path) -> None:
    s = store(tmp_path, (0.5, False))
    html = render_history_html(read_history(s), paths=["scores.quality", "metrics.none"])
    assert html.count("<figure>") == 2 and "no values for this path" in html


def test_mixed_datasets_warning(tmp_path: Path) -> None:
    s = tmp_path / "h"
    record_run(make_run(tmp_path / "a", 0.5, False, dataset="x"), s, recorded_at=T0)
    record_run(make_run(tmp_path / "b", 0.6, False, dataset="y"), s, recorded_at=T0)
    assert "more than one dataset" in render_history_html(read_history(s))


def test_compare_kind_leaves_out_invalid(tmp_path: Path) -> None:
    s = tmp_path / "h"
    record_report(
        evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml"), s, recorded_at=T0
    )
    record_report(
        evalgate.compare(tmp_path / "x", tmp_path / "y", tmp_path / "p.yaml"), s, recorded_at=T0
    )
    html = render_history_html(read_history(s), kind="compare")
    assert "1 entry" in html and "INVALID compares are left out" in html


def test_hostile_labels_and_safe_markup(tmp_path: Path) -> None:
    s = tmp_path / "h"
    hostile = "</script><img src=x onerror=alert(1)>"
    record_run(make_run(tmp_path / "a", 0.5, True), s, recorded_at=T0, label=hostile)
    html = render_history_html(read_history(s))
    assert hostile not in html
    m = parse(html)
    assert sum(tag == "script" for tag, _ in m.tags) == 1
    for tag, attrs in m.tags:
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)
        for name in ("src", "href"):
            if name in attrs:
                assert (attrs[name] or "").startswith("data:")
    [script] = m.scripts
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in html


def test_same_bytes_and_empty_selection(tmp_path: Path) -> None:
    s = store(tmp_path, (0.5, False), (0.6, True))
    entries = read_history(s)
    assert render_history_html(entries) == render_history_html(entries)
    assert "No entries match this selection" in render_history_html(entries, dataset="nope")


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_writes_the_page(tmp_path: Path) -> None:
    s = store(tmp_path, (0.5, True), (0.6, True))
    out = tmp_path / "page.html"
    result = runner.invoke(
        app, ["history", "report", str(out), "--history", str(s), "--path", "scores.quality"]
    )
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert text.count("<figure>") == 1 and "<td>c1</td><td>safe</td>" in text
    assert b"\r" not in out.read_bytes()


def test_cli_empty_selection_still_writes(tmp_path: Path) -> None:
    s = store(tmp_path, (0.5, False))
    out = tmp_path / "page.html"
    result = runner.invoke(
        app, ["history", "report", str(out), "--history", str(s), "--dataset", "nope"]
    )
    assert result.exit_code == 0 and "No entries match" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", ["missing", "unwritable"])
def test_cli_failures_exit_2(tmp_path: Path, case: str) -> None:
    if case == "missing":
        result = runner.invoke(
            app,
            ["history", "report", str(tmp_path / "p.html"), "--history", str(tmp_path / "none")],
        )
        assert result.exit_code == EXIT_INVALID and "history_missing" in result.output
    else:
        s = store(tmp_path, (0.5, False))
        (tmp_path / "dir.html").mkdir()
        result = runner.invoke(
            app, ["history", "report", str(tmp_path / "dir.html"), "--history", str(s)]
        )
        assert result.exit_code == EXIT_INVALID and "cannot write" in result.output


def test_two_runs_around_a_gap_are_two_polylines() -> None:
    svg = svg_chart("p", [(1, "", 1.0), (2, "", 2.0), (3, "", None), (4, "", 3.0), (5, "", 4.0)])
    assert svg.count("<polyline") == 2 and len(circles(svg)) == 4


def test_extreme_values_stay_finite() -> None:
    svg = svg_chart("p", [(1, "", -1.7e308), (2, "", 1.7e308)])
    assert "nan" not in svg and "inf" not in svg
    assert circles(svg) == [("80.00", "160.00"), ("600.00", "40.00")]


def test_failures_table_rows_equal_recurring_failures(tmp_path: Path) -> None:
    s = store(tmp_path, (0.5, True), (0.6, True), (0.7, True))
    entries = read_history(s)
    html = render_history_html(entries)
    body = html.split("<h2>Recurring failures</h2>")[1].split("<tbody>")[1].split("</tbody>")[0]
    rows = [re.findall(r"<td[^>]*>(.*?)</td>", r) for r in re.findall(r"<tr>(.*?)</tr>", body)]
    expected = [
        [
            p["case_id"],
            p["check"],
            str(p["failing"]),
            str(p["of"]),
            str(p["first"]),
            str(p["last"]),
            str(p["streak"]),
        ]
        for p in recurring_failures(entries)["pairs"]
    ]
    assert rows == expected == [["c1", "safe", "3", "3", "1", "3", "3"]]


def test_hostile_path_case_and_check_on_the_page(tmp_path: Path) -> None:
    hostile = "<img src=x onerror=alert(1)>"
    s = tmp_path / "h"
    for i in range(2):
        path = tmp_path / f"r{i}"
        with EvalRun(
            path.name, dataset="d", dataset_version="v", evaluator_version="e", path=path
        ) as r:
            r.record(hostile, scores={hostile: 1.0}, checks={hostile: False})
        record_run(path, s, recorded_at=T0)
    html = render_history_html(read_history(s))
    assert hostile not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    m = parse(html)
    for tag, attrs in m.tags:
        assert not any(name.startswith("on") for name in attrs), (tag, attrs)
        for name in ("src", "href", "action", "srcset"):
            if name in attrs:
                assert (attrs[name] or "").startswith("data:")
        style = attrs.get("style") or ""
        assert "url(" not in style and "@import" not in style


def test_no_mixed_dataset_warning_for_one_dataset(tmp_path: Path) -> None:
    assert "more than one dataset" not in render_history_html(
        read_history(store(tmp_path, (0.5, False), (0.6, False)))
    )


def test_duplicate_paths_chart_once(tmp_path: Path) -> None:
    html = render_history_html(
        read_history(store(tmp_path, (0.5, False))), paths=["scores.quality"] * 2
    )
    assert html.count("<figure>") == 1
