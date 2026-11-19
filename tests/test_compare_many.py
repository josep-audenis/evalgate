"""compare_many and ranking (phase-7 §3, 0.11.0 T1)."""

import json
import shutil
from pathlib import Path

import pytest

import evalgate
from evalgate import compare, compare_many
from evalgate.comparison.ranking import exit_code
from evalgate.models import Report

QS = Path(__file__).parent.parent / "examples" / "quickstart"
BASE, FAIL_C, POLICY = QS / "baseline", QS / "candidate", QS / "evalgate.yaml"


def with_errored_case(tmp_path: Path) -> Path:
    """The baseline as a candidate, with one case errored: FAIL, fewer compared cases."""
    cand = tmp_path / "errored"
    shutil.copytree(BASE, cand)
    lines = (cand / "cases.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    lines[0] = json.dumps({"case_id": first["case_id"], "status": "error", "error": "timeout"})
    (cand / "cases.jsonl").write_text("\n".join(lines) + "\n")
    return cand


def test_each_report_equals_a_two_run_compare(tmp_path: Path) -> None:
    cands = [FAIL_C, BASE, tmp_path / "missing"]
    result = compare_many(BASE, cands, POLICY)
    assert [r.model_dump_json() for r in result.reports] == [
        compare(BASE, c, POLICY).model_dump_json() for c in cands
    ]
    assert [r.status for r in result.reports] == ["FAIL", "PASS", "INVALID"]


@pytest.mark.parametrize(
    ("statuses", "code"),
    [
        (["PASS"], 0),
        (["PASS", "PASS"], 0),
        (["PASS", "FAIL"], 1),
        (["FAIL", "FAIL"], 1),
        (["PASS", "INVALID"], 2),
        (["FAIL", "INVALID"], 2),
        (["INVALID"], 2),
    ],
)
def test_exit_code_aggregation(statuses: list[str], code: int) -> None:
    reports = [Report(evalgate_version="x", status=s) for s in statuses]  # type: ignore[arg-type]
    assert exit_code(reports) == code


def test_ranking_maximize_includes_fail_excludes_invalid(tmp_path: Path) -> None:
    result = compare_many(BASE, [BASE, tmp_path / "missing", FAIL_C], POLICY, rank_by="quality")
    assert result.exit_code == 2
    assert result.ranking is not None
    ranked = [(i.rank, i.n, i.status) for i in result.ranking]
    assert ranked == [(1, 3, "FAIL"), (2, 1, "PASS")]  # FAIL has the higher quality
    assert result.ranking[0].value > result.ranking[1].value
    assert result.ranking[0].cases == 4


def test_ranking_minimize() -> None:
    result = compare_many(BASE, [FAIL_C, BASE], POLICY, rank_by="latency")
    assert result.ranking is not None
    values = [i.value for i in result.ranking]
    assert values == sorted(values)  # lowest latency first


@pytest.mark.parametrize("metric", ["quality", "latency"])
def test_ties_keep_command_line_order(metric: str) -> None:
    result = compare_many(BASE, [FAIL_C, FAIL_C, FAIL_C], POLICY, rank_by=metric)
    assert result.ranking is not None
    assert [i.n for i in result.ranking] == [1, 2, 3]
    assert [i.rank for i in result.ranking] == [1, 2, 3]  # no shared ranks


def test_all_invalid_ranks_empty_and_no_rank_by_is_none(tmp_path: Path) -> None:
    assert compare_many(BASE, [tmp_path / "x"], POLICY, rank_by="quality").ranking == []
    assert compare_many(BASE, [FAIL_C], POLICY).ranking is None


def test_different_case_counts_are_flagged(tmp_path: Path) -> None:
    result = compare_many(BASE, [BASE, with_errored_case(tmp_path)], POLICY, rank_by="quality")
    assert result.ranking is not None
    assert sorted(i.cases for i in result.ranking) == [3, 4]
    assert result.summary().cases_differ is True
    assert (
        compare_many(BASE, [BASE, FAIL_C], POLICY, rank_by="quality").summary().cases_differ
        is False
    )


def test_unloadable_candidate_has_null_run_id(tmp_path: Path) -> None:
    summary = compare_many(BASE, [tmp_path / "x", BASE], POLICY).summary()
    assert [(c.n, c.run_id, c.status) for c in summary.candidates] == [
        (1, None, "INVALID"),
        (2, "baseline-v1", "PASS"),
    ]
    assert summary.baseline_run_id == "baseline-v1"


@pytest.mark.parametrize("bad", ["examples/quickstart/candidate", FAIL_C])
def test_single_path_is_a_type_error(bad: object) -> None:
    with pytest.raises(TypeError):
        compare_many(BASE, bad, POLICY)  # type: ignore[arg-type]


def test_empty_candidates_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        compare_many(BASE, [], POLICY)


def test_unknown_rank_by(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a policy metric"):
        compare_many(BASE, [FAIL_C], POLICY, rank_by="nope")
    # An unloadable policy: every report is INVALID, the name isn't checked.
    result = compare_many(BASE, [FAIL_C], tmp_path / "missing.yaml", rank_by="nope")
    assert result.exit_code == 2 and result.ranking == []


def test_summary_is_deterministic(tmp_path: Path) -> None:
    def write(name: str) -> bytes:
        summary = compare_many(BASE, [FAIL_C, BASE], POLICY, rank_by="quality").summary()
        evalgate.io.write_json_report(summary, tmp_path / name)
        return (tmp_path / name).read_bytes()

    first = write("a.json")
    assert first == write("b.json")
    data = json.loads(first)
    assert list(data) == [
        "summary_version",
        "evalgate_version",
        "baseline_run_id",
        "exit_code",
        "rank_by",
        "candidates",
        "ranking",
        "cases_differ",
    ]
    assert b"\r" not in first


def test_candidate_equal_to_baseline_and_repeated() -> None:
    result = compare_many(BASE, [BASE, BASE], POLICY)
    assert [r.status for r in result.reports] == ["PASS", "PASS"] and result.exit_code == 0
