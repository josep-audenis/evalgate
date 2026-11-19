"""comparison.failure_groups (phase-7 §4, 0.12.0 T1)."""

import json
from pathlib import Path
from typing import Any

from conftest import MANIFEST

import evalgate
from evalgate.comparison import compare
from evalgate.engine import evaluate
from evalgate.models import Policy, Report
from evalgate.models.run import CaseResult, Run, RunManifest

QS = Path(__file__).parent.parent / "examples" / "quickstart"


def run(*cases: dict[str, Any], **manifest: Any) -> Run:
    return Run(
        manifest=RunManifest(**(MANIFEST | manifest)),
        cases=[CaseResult(**({"status": "ok"} | c)) for c in cases],
    )


def c(case_id: str, category: object = None, **checks: bool) -> dict[str, Any]:
    meta = {} if category is None else {"metadata": {"category": category}}
    return {"case_id": case_id, "checks": checks} | meta


def groups(base: Run, cand: Run) -> Any:
    g = compare(base, cand, {}, 0.95).failure_groups
    assert g is not None
    return g


def test_exact_groups_buckets_and_invariant() -> None:
    base = run(
        c("a", "billing", safe=True, fmt=True),
        c("b", "billing", safe=False, fmt=True),
        c("c", "shipping", safe=True),
        c("d", None, safe=True),
    )
    cand = run(
        c("a", "billing", safe=False, fmt=True),  # new
        c("b", "billing", safe=False, fmt=False),  # persistent + new
        c("c", "shipping", safe=True, extra=False),  # no_baseline
        c("d", None, safe=False),  # new, uncategorized
    )
    g = groups(base, cand)
    by_check = {x.check: x for x in g.by_check}
    assert (by_check["safe"].failing, by_check["safe"].new, by_check["safe"].persistent) == (
        3,
        2,
        1,
    )
    assert by_check["safe"].cases == ["a", "b", "d"]
    assert (by_check["extra"].no_baseline, by_check["extra"].cases) == (1, ["c"])
    assert [x.check for x in g.by_check] == ["safe", "extra", "fmt"]  # failing desc, then name
    billing = next(x for x in g.by_category if x.category == "billing")
    assert (billing.failing, billing.checks, billing.cases) == (3, ["fmt", "safe"], ["a", "b"])
    assert [(x.check, x.category, x.failing) for x in g.by_check_category] == [
        ("safe", "billing", 2),
        ("extra", "shipping", 1),
        ("fmt", "billing", 1),
    ]
    assert g.uncategorized == 1
    for x in (*g.by_check, *g.by_category, *g.by_check_category):
        assert x.failing == x.new + x.persistent + x.no_baseline


def test_consistent_with_check_stats() -> None:
    d = QS
    report = evalgate.compare(d / "baseline", d / "candidate", d / "evalgate.yaml")
    assert report.comparison is not None and report.comparison.failure_groups is not None
    stats = {s.check: s for s in report.comparison.check_stats}
    for g in report.comparison.failure_groups.by_check:
        assert g.new == stats[g.check].newly_failing
        assert g.failing - g.no_baseline == stats[g.check].candidate_failures


def test_only_compared_cases() -> None:
    base = run(c("a", safe=True), {"case_id": "b", "status": "error", "error": "x"})
    cand = run(c("a", safe=True), c("b", safe=False), c("z", safe=False))  # z: candidate only
    g = groups(base, cand)
    assert g.by_check == [] and g.uncategorized == 0


def test_baseline_category_wins_and_odd_categories_are_uncategorized() -> None:
    base = run(c("a", "billing", s=True), c("b", None, s=True), c("e", 7, s=True))
    cand = run(c("a", "shipping", s=False), c("b", "billing", s=False), c("e", 7, s=False))
    g = groups(base, cand)
    assert [(x.category, x.cases) for x in g.by_category] == [("billing", ["a"])]
    assert g.uncategorized == 2  # b: category only on the candidate; e: not a string


def test_ties_sort_by_key_and_nothing_failing_is_empty() -> None:
    base = run(c("a", "y", s=True, t=True), c("b", "x", s=True, t=True))
    cand = run(c("a", "y", s=False, t=True), c("b", "x", s=True, t=False))
    g = groups(base, cand)
    assert [x.check for x in g.by_check] == ["s", "t"]
    assert [x.category for x in g.by_category] == ["x", "y"]
    ok = groups(base, base)
    assert (ok.by_check, ok.by_category, ok.by_check_category, ok.uncategorized) == ([], [], [], 0)


def test_repeats_are_collapsed_first() -> None:
    policy = Policy.model_validate(
        {"version": 1, "comparison": {"min_compared_cases": 1}, "checks": {"max_new_failures": 5}}
    )
    base = run(
        c("a", s=True) | {"repeat": 0},
        c("a", s=True) | {"repeat": 1},
        schema_version="1.1",
    )
    cand = run(
        c("a", s=True) | {"repeat": 0},
        c("a", s=False) | {"repeat": 1},
        schema_version="1.1",
    )
    report = evaluate(base, cand, policy)
    assert report.comparison is not None and report.comparison.failure_groups is not None
    [g] = report.comparison.failure_groups.by_check
    assert (g.check, g.failing, g.new) == ("s", 1, 1)


def test_invalid_before_comparing_has_no_comparison(tmp_path: Path) -> None:
    assert evalgate.compare(tmp_path / "x", tmp_path / "y", tmp_path / "p.yaml").comparison is None


def test_pre_0_12_report_still_loads() -> None:
    report = evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml")
    data = json.loads(report.model_dump_json())
    del data["comparison"]["failure_groups"]
    old = Report.model_validate(data)
    assert old.comparison is not None and old.comparison.failure_groups is None
