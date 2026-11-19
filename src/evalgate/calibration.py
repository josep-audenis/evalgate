"""Judge calibration: agreement between a run's checks/scores and human labels. Pure.

Reporting only (docs/design/native-evaluators.md §11): nothing here affects a decision.
"""

from collections import defaultdict
from collections.abc import Sequence
from fractions import Fraction
from math import fsum, sqrt
from typing import Literal

from evalgate.models.calibration import CalibrationEntry, CalibrationReport, LabelLine
from evalgate.models.report import Diagnostic
from evalgate.models.run import CaseResult, Run

Kind = Literal["check", "score"]
_SECTION: dict[Kind, str] = {"check": "checks", "score": "scores"}
_OTHER: dict[Kind, Kind] = {"check": "score", "score": "check"}


def _error(code: str, message: str) -> Diagnostic:
    return Diagnostic(level="error", code=code, message=message)


def kappa(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for two binary raters; None when expected agreement is 1 (or no pairs).
    Exact arithmetic, so the pe == 1 test is not a float comparison."""
    n = len(pairs)
    if not n:
        return None
    po = Fraction(sum(a == b for a, b in pairs), n)
    p_a = Fraction(sum(a for a, _ in pairs), n)
    p_b = Fraction(sum(b for _, b in pairs), n)
    pe = p_a * p_b + (1 - p_a) * (1 - p_b)
    return None if pe == 1 else float((po - pe) / (1 - pe))


def _ranks(values: Sequence[float]) -> list[float]:
    """Average (fractional) ranks, 1-based: ties share the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(pairs: Sequence[tuple[float, float]]) -> float | None:
    """Pearson correlation of average ranks (correct with ties); None if n < 2 or constant."""
    if len(pairs) < 2:
        return None
    xs = _ranks([a for a, _ in pairs])
    ys = _ranks([b for _, b in pairs])
    mx, my = fsum(xs) / len(xs), fsum(ys) / len(ys)
    sxx = fsum((x - mx) ** 2 for x in xs)
    syy = fsum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return fsum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sqrt(sxx * syy)


def _entry(
    kind: Kind,
    name: str,
    labels: Sequence[LabelLine],
    rows: dict[str, list[CaseResult]],
) -> CalibrationEntry:
    section = _SECTION[kind]
    pairs: list[tuple[float, float]] = []  # (human, run); bools for checks
    cases: set[str] = set()
    errors = missing = 0
    for line in labels:
        human = getattr(line, section).get(name)
        if human is None:
            continue
        before_pairs, before_errors = len(pairs), errors
        for row in rows.get(line.case_id, []):
            if row.status == "error":
                errors += 1
            elif (value := getattr(row, section).get(name)) is not None:
                pairs.append((human, value))
        if len(pairs) > before_pairs:
            cases.add(line.case_id)
        elif errors == before_errors:
            missing += 1
    n = len(pairs)
    figures: dict[str, float | None] = {}
    if n and kind == "check":
        bools = [(bool(a), bool(b)) for a, b in pairs]
        figures = {"accuracy": sum(a == b for a, b in bools) / n, "kappa": kappa(bools)}
    elif n:
        figures = {"mae": fsum(abs(a - b) for a, b in pairs) / n, "spearman": spearman(pairs)}
    return CalibrationEntry(
        kind=kind,
        name=name,
        n=n,
        n_cases=len(cases),
        errors=errors,
        missing=missing,
        **figures,
    )


def calibrate(run: Run, labels: Sequence[LabelLine]) -> tuple[CalibrationReport, list[Diagnostic]]:
    """Compare the run with human labels, one entry per labelled (kind, name), sorted.

    Diagnostics (all errors; then the report must not be used): `labels_unknown` for a label
    no case has and the manifest doesn't declare (or that only exists as the other kind),
    `labels_empty` when nothing could be paired.
    """
    rows: dict[str, list[CaseResult]] = defaultdict(list)
    for case in run.cases:
        rows[case.case_id].append(case)
    present: dict[Kind, set[str]] = {
        "check": {k for c in run.cases for k in c.checks},
        "score": {k for c in run.cases for k in c.scores},
    }
    declared = set(run.manifest.evaluators or {})
    wanted: list[tuple[Kind, str]] = sorted(
        {("check", k) for line in labels for k in line.checks}
        | {("score", k) for line in labels for k in line.scores}
    )
    diagnostics = [
        _error(
            "labels_unknown",
            f"{_SECTION[kind]}.{name}: labelled, but no case in run {run.manifest.run_id} has it"
            + (
                f" (only {_SECTION[_OTHER[kind]]}.{name})"
                if name in present[_OTHER[kind]]
                else " and the manifest doesn't declare it"
            ),
        )
        for kind, name in wanted
        # Declared names carry no kind: one only present as the other kind is still a typo.
        if name not in present[kind] and (name not in declared or name in present[_OTHER[kind]])
    ]
    entries = [_entry(kind, name, labels, rows) for kind, name in wanted]
    report = CalibrationReport(
        run_id=run.manifest.run_id, labelled_cases=len(labels), entries=entries
    )
    if not diagnostics and not any(e.n for e in entries):
        diagnostics.append(
            _error("labels_empty", "no labelled case has a value in the run to compare with")
        )
    return report, diagnostics
