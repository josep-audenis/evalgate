"""Calibration engine and labels reader (native-evaluators §11, T3).

Every figure is checked against values worked out by hand in the comments.
"""

import json
from math import sqrt
from pathlib import Path
from typing import Any

import pytest
from conftest import MANIFEST

from evalgate import EvalRun
from evalgate.calibration import calibrate, kappa, spearman
from evalgate.io.labels import calibrate_run, read_labels
from evalgate.io.reader import InputError
from evalgate.models.calibration import CalibrationEntry, LabelLine
from evalgate.models.run import CaseResult, Run, RunManifest

T, F = True, False


def run(*cases: dict[str, Any], **manifest: Any) -> Run:
    return Run(
        manifest=RunManifest(**(MANIFEST | manifest)),
        cases=[CaseResult(**({"status": "ok"} | c)) for c in cases],
    )


def label(case_id: str, **kwargs: Any) -> LabelLine:
    return LabelLine(case_id=case_id, **kwargs)


def entries(r: Run, labels: list[LabelLine]) -> dict[tuple[str, str], CalibrationEntry]:
    report, diagnostics = calibrate(r, labels)
    assert diagnostics == []
    return {(e.kind, e.name): e for e in report.entries}


# --- kappa -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([(T, T), (F, F)], 1.0),  # po 1, pe .5
        ([(T, T), (T, F), (F, T), (F, F)], 0.0),  # po .5 = pe .5
        ([(T, F), (F, T)], -1.0),  # po 0, pe .5
        ([(T, F), (T, F)], 0.0),  # constant, opposite: po 0, pe 0
        ([(T, T), (T, T), (T, F)], 0.0),  # one constant: po 2/3, pa 1, pb 2/3, pe 2/3
        ([(T, T), (T, T), (F, T), (F, F)], 0.5),  # po 3/4, pa 1/2, pb 3/4, pe 1/2
    ],
)
def test_kappa_hand_computed(pairs: list[tuple[bool, bool]], expected: float) -> None:
    assert kappa(pairs) == pytest.approx(expected)


@pytest.mark.parametrize("pairs", [[(T, T), (T, T)], [(F, F)], []])
def test_kappa_undefined_when_pe_is_1(pairs: list[tuple[bool, bool]]) -> None:
    assert kappa(pairs) is None


# --- spearman --------------------------------------------------------------------------------


def test_spearman_with_ties() -> None:
    # human [1,2,2,3] -> ranks [1,2.5,2.5,4]; judge [1,3,2,4] -> ranks [1,3,2,4]; means 2.5.
    # sxy = 2.25+0+0+2.25 = 4.5; sxx = 4.5; syy = 5 -> 4.5 / sqrt(22.5) = sqrt(0.9).
    assert spearman([(1, 1), (2, 3), (2, 2), (3, 4)]) == pytest.approx(sqrt(0.9))


def test_spearman_perfect_and_inverse() -> None:
    assert spearman([(1, 10), (2, 20), (3, 25)]) == pytest.approx(1.0)
    assert spearman([(1, 3), (2, 2), (3, 1)]) == pytest.approx(-1.0)


@pytest.mark.parametrize("pairs", [[], [(1, 2)], [(1, 2), (1, 3)], [(1, 2), (2, 2)]])
def test_spearman_undefined(pairs: list[tuple[float, float]]) -> None:
    assert spearman(pairs) is None


# --- calibrate -------------------------------------------------------------------------------


def test_check_and_score_entries() -> None:
    r = run(
        {"case_id": "a", "checks": {"ok": T}, "scores": {"q": 1}},
        {"case_id": "b", "checks": {"ok": T}, "scores": {"q": 3}},
        {"case_id": "c", "checks": {"ok": T}, "scores": {"q": 2}},
        {"case_id": "d", "checks": {"ok": F}, "scores": {"q": 4}},
    )
    labels = [
        label("a", checks={"ok": T}, scores={"q": 1}),
        label("b", checks={"ok": T}, scores={"q": 2}),
        label("c", checks={"ok": F}, scores={"q": 2}),
        label("d", checks={"ok": F}, scores={"q": 3}),
    ]
    e = entries(r, labels)
    assert list(e) == [("check", "ok"), ("score", "q")]  # sorted by (kind, name)
    check = e["check", "ok"]
    assert (check.n, check.n_cases, check.errors, check.missing) == (4, 4, 0, 0)
    assert check.accuracy == 0.75 and check.kappa == pytest.approx(0.5)
    assert check.mae is None and check.spearman is None
    score = e["score", "q"]
    assert score.mae == 0.5  # |0|+|1|+|0|+|1| / 4
    assert score.spearman == pytest.approx(sqrt(0.9))
    assert score.accuracy is None and score.kappa is None


def test_repeats_are_pooled_and_errors_excluded() -> None:
    r = run(
        {"case_id": "a", "repeat": 0, "checks": {"ok": T}},
        {"case_id": "a", "repeat": 1, "status": "error", "error": "x"},
        {"case_id": "a", "repeat": 2, "checks": {"ok": F}},
        {"case_id": "b", "repeat": 0, "checks": {"ok": T}},
        {"case_id": "b", "repeat": 1, "checks": {"ok": T}},
        {"case_id": "b", "repeat": 2, "checks": {"ok": T}},
        schema_version="1.1",
    )
    e = entries(r, [label("a", checks={"ok": T}), label("b", checks={"ok": T})])["check", "ok"]
    assert (e.n, e.n_cases, e.errors, e.missing) == (5, 2, 1, 0)
    assert e.accuracy == 0.8  # 4 of 5 rows agree
    assert e.kappa == 0.0  # po 4/5, pa 1, pb 4/5, pe 4/5


def test_pooled_kappa_is_defined_when_one_rater_constant() -> None:
    r = run(
        {"case_id": "a", "repeat": 0, "checks": {"ok": T}},
        {"case_id": "a", "repeat": 1, "checks": {"ok": F}},
        schema_version="1.1",
    )
    e = entries(r, [label("a", checks={"ok": T})])["check", "ok"]
    # po 1/2, pa 1, pb 1/2, pe 1/2 -> kappa 0
    assert e.kappa == 0.0 and e.n == 2 and e.n_cases == 1


def test_missing_counts() -> None:
    r = run({"case_id": "a", "checks": {"ok": T}}, {"case_id": "b", "checks": {"other": T}})
    labels = [
        label("a", checks={"ok": T}),
        label("b", checks={"ok": T}),  # present, no value
        label("zz", checks={"ok": T}),  # absent from the run
    ]
    e = entries(r, labels)["check", "ok"]
    assert (e.n, e.missing, e.errors) == (1, 2, 0)


def test_n_zero_entry_has_null_figures_next_to_a_real_one() -> None:
    r = run(
        {"case_id": "a", "checks": {"ok": T}},
        {"case_id": "b", "status": "error", "error": "evaluator j failed"},
        evaluators={"j": "1", "ok": "1"},
        schema_version="1.2",
    )
    e = entries(r, [label("a", checks={"ok": T}), label("b", scores={"j": 3})])
    empty = e["score", "j"]
    assert (empty.n, empty.errors) == (0, 1)
    assert empty.mae is None and empty.spearman is None
    assert e["check", "ok"].n == 1


def test_unknown_label_name() -> None:
    r = run({"case_id": "a", "scores": {"x": 1}})
    report, diagnostics = calibrate(r, [label("a", checks={"x": T})])
    assert [d.code for d in diagnostics] == ["labels_unknown"]
    assert "checks.x" in diagnostics[0].message


def test_declared_name_with_the_other_kind_is_unknown() -> None:
    r = run(
        {"case_id": "a", "scores": {"x": 1}, "checks": {"y": T}},
        evaluators={"x": "1", "y": "1"},
        schema_version="1.2",
    )
    _, diagnostics = calibrate(r, [label("a", checks={"x": T, "y": T})])
    assert [d.code for d in diagnostics] == ["labels_unknown"]
    assert "checks.x" in diagnostics[0].message


def test_errored_and_valueless_repeats_count_as_errors_only() -> None:
    # Precedence: pairs > errors > missing. An errored repeat plus an ok repeat without the
    # name counts once, in errors; the valueless ok row is not reported separately.
    r = run(
        {"case_id": "a", "repeat": 0, "status": "error", "error": "x"},
        {"case_id": "a", "repeat": 1, "checks": {"other": T}},
        {"case_id": "b", "repeat": 0, "checks": {"ok": T}},
        {"case_id": "b", "repeat": 1, "checks": {"ok": T}},
        schema_version="1.1",
    )
    e = entries(r, [label("a", checks={"ok": T}), label("b", checks={"ok": T})])["check", "ok"]
    assert (e.n, e.errors, e.missing) == (2, 1, 0)


def test_declared_but_all_errored_is_not_unknown_but_is_empty() -> None:
    r = run(
        {"case_id": "a", "status": "error", "error": "evaluator j failed"},
        evaluators={"j": "1"},
        schema_version="1.2",
    )
    report, diagnostics = calibrate(r, [label("a", checks={"j": T})])
    assert [d.code for d in diagnostics] == ["labels_empty"]
    assert report.entries[0].errors == 1


def test_deterministic_order() -> None:
    r = run({"case_id": "a", "checks": {"b": T, "a": T}, "scores": {"a": 1}})
    labels = [label("a", scores={"a": 1}, checks={"b": T, "a": F})]
    first = calibrate(r, labels)[0]
    assert [(e.kind, e.name) for e in first.entries] == [
        ("check", "a"),
        ("check", "b"),
        ("score", "a"),
    ]
    assert first == calibrate(r, list(reversed(labels)))[0]


# --- labels reader ---------------------------------------------------------------------------

SECRET = "SENTINEL-c4e1-label"


def write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "labels.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def codes(path: Path) -> list[str]:
    with pytest.raises(InputError) as exc:
        read_labels(path)
    assert SECRET not in str(exc.value)
    return [d.code for d in exc.value.diagnostics]


def test_reads_labels(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '{"case_id": "a", "checks": {"ok": true}}',
        "",
        '{"case_id": "b", "scores": {"q": 2}}',
    )
    assert [x.case_id for x in read_labels(path)] == ["a", "b"]


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (f'{{"case_id": "{SECRET}", "checks": {{"ok": true}}', "labels_parse"),
        (f'{{"case_id": "a", "checks": {{"{SECRET}": true, "{SECRET}": false}}}}', "labels_parse"),
        ('{"case_id": "a", "scores": {"q": NaN}}', "labels_parse"),
        ('{"case_id": "a", "scores": {"q": 1e999}}', "labels_parse"),
        ("[1]", "labels_parse"),
        ('{"case_id": "a"}', "labels_record"),
        ('{"case_id": "a", "checks": {}, "scores": {}}', "labels_record"),
        ('{"case_id": "a", "scores": {"q": true}}', "labels_record"),
        ('{"case_id": "a", "checks": {"ok": 1}}', "labels_record"),
        (f'{{"case_id": "a", "checks": {{"ok": true}}, "note": "{SECRET}"}}', "labels_record"),
    ],
)
def test_bad_label_lines(tmp_path: Path, line: str, code: str) -> None:
    assert code in codes(write(tmp_path, line))


def test_duplicate_case_id_and_line_numbers(tmp_path: Path) -> None:
    good = '{"case_id": "a", "checks": {"ok": true}}'
    path = write(tmp_path, good, "nope", good)
    with pytest.raises(InputError) as exc:
        read_labels(path)
    messages = [d.message for d in exc.value.diagnostics]
    assert any("line 2" in m and "not valid JSON" in m for m in messages)
    assert sum("duplicate case_id" in m for m in messages) == 2


def test_empty_labels_file(tmp_path: Path) -> None:
    assert codes(write(tmp_path, "")) == ["labels_empty"]


# --- calibrate_run ---------------------------------------------------------------------------


def make_run(path: Path, cases: list[tuple[str, bool]]) -> Path:
    with EvalRun("r", dataset="d", dataset_version="v", evaluator_version="e", path=path) as r:
        for case_id, ok in cases:
            r.record(case_id, checks={"ok": ok})
    return path


def test_calibrate_run(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run", [("a", True), ("b", False)])
    labels = write(
        tmp_path,
        '{"case_id": "a", "checks": {"ok": true}}',
        '{"case_id": "b", "checks": {"ok": true}}',
    )
    report = calibrate_run(run_dir, labels)
    assert report.run_id == "r" and report.labelled_cases == 2
    assert report.entries[0].accuracy == 0.5
    assert json.loads(report.model_dump_json())["entries"][0]["name"] == "ok"


def test_invalid_run_is_not_calibrated(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path / "run", [("a", True)])
    manifest = json.loads((run_dir / "run.json").read_text())
    (run_dir / "run.json").write_text(
        json.dumps(manifest | {"repeats": 3, "schema_version": "1.1"})
    )
    labels = write(tmp_path, '{"case_id": "a", "checks": {"ok": true}}')
    with pytest.raises(InputError):
        calibrate_run(run_dir, labels)
