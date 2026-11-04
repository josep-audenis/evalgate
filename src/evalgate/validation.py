"""Pure validation of runs and run pairs. Any `error` diagnostic makes the comparison INVALID."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from evalgate.models import SCHEMA_VERSION_REPEATS, CaseResult, Diagnostic, Policy, Run

_MAX_IDS_SHOWN = 5
# shortcut: fixed rule-of-thumb sample size, replace with interval-based checks (bootstrap CIs).
SMALL_SAMPLE = 30


def _ids(ids: Iterable[str]) -> str:
    ids = sorted(ids)
    shown = ", ".join(ids[:_MAX_IDS_SHOWN])
    return shown + (f" (+{len(ids) - _MAX_IDS_SHOWN} more)" if len(ids) > _MAX_IDS_SHOWN else "")


def _diag(error: bool, code: str, message: str) -> Diagnostic:
    return Diagnostic(level="error" if error else "warning", code=code, message=message)


def validate_run(run: Run, label: str = "run") -> list[Diagnostic]:
    """Structure of one run, including repeats (see docs/design/repeated-runs.md)."""
    out: list[Diagnostic] = []
    manifest = run.manifest
    with_repeat = sum(c.repeat is not None for c in run.cases)
    if with_repeat and with_repeat != len(run.cases):
        out.append(_diag(True, "repeat_mixed", f"{label}: some cases have `repeat`, others don't"))
    uses_repeats = with_repeat > 0 or manifest.repeats is not None
    if uses_repeats and manifest.schema_version != SCHEMA_VERSION_REPEATS:
        out.append(
            _diag(
                True,
                "repeat_schema",
                f"{label}: repeats require schema_version {SCHEMA_VERSION_REPEATS!r}",
            )
        )
    if out:
        return out

    counts = Counter((c.case_id, c.repeat) for c in run.cases)
    dupes = {case_id for (case_id, _), n in counts.items() if n > 1}
    if dupes:
        what = "(case_id, repeat)" if with_repeat else "case_id"
        return [_diag(True, "duplicate_case_id", f"{label}: duplicate {what}: {_ids(dupes)}")]

    if uses_repeats:
        by_case: dict[str, list[int]] = defaultdict(list)
        for c in run.cases:
            by_case[c.case_id].append(c.repeat if c.repeat is not None else 0)
        k = manifest.repeats or max(len(r) for r in by_case.values())
        bad = [i for i, r in by_case.items() if sorted(r) != list(range(k))]
        if bad:
            out.append(
                _diag(
                    True,
                    "repeat_incomplete",
                    f"{label}: {len(bad)} case(s) do not have exactly repeats 0..{k - 1}: "
                    + _ids(bad),
                )
            )

        # Repeats that ran must report the same fields: combining drops a field missing from
        # any repeat, which could hide a failure (a check present only in some repeats).
        def fields(c: CaseResult) -> tuple[frozenset[str], ...]:
            return frozenset(c.scores), frozenset(c.metrics), frozenset(c.checks)

        ok: dict[str, list[CaseResult]] = defaultdict(list)
        for c in run.cases:
            if c.status == "ok":
                ok[c.case_id].append(c)
        uneven = [i for i, reps in ok.items() if len({fields(r) for r in reps}) > 1]
        if uneven:
            out.append(
                _diag(
                    True,
                    "repeat_inconsistent",
                    f"{label}: {len(uneven)} case(s) whose repeats report different scores, "
                    f"metrics or checks: {_ids(uneven)}",
                )
            )
        meta: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in run.cases:
            meta[c.case_id].append(c.metadata)
        differing = [i for i, ms in meta.items() if any(m != ms[0] for m in ms)]
        if differing:
            out.append(
                _diag(
                    False,
                    "repeat_metadata",
                    f"{label}: {len(differing)} case(s) with different metadata across repeats; "
                    f"repeat 0's metadata (e.g. category) is used: {_ids(differing)}",
                )
            )
    return out


def validate_pair(baseline: Run, candidate: Run, policy: Policy) -> list[Diagnostic]:
    """Check two individually valid runs are comparable under `policy`."""
    out: list[Diagnostic] = []
    bm, cm, rules = baseline.manifest, candidate.manifest, policy.comparison

    # Mismatches always surface: an error when required, a warning when the policy opted out.
    if (bm.dataset_id, bm.dataset_version) != (cm.dataset_id, cm.dataset_version):
        out.append(
            _diag(
                rules.require_same_dataset,
                "dataset_mismatch",
                f"dataset differs: baseline {bm.dataset_id}@{bm.dataset_version}, "
                f"candidate {cm.dataset_id}@{cm.dataset_version}",
            )
        )
    if bm.evaluator_version != cm.evaluator_version:
        out.append(
            _diag(
                rules.require_same_evaluator,
                "evaluator_mismatch",
                f"evaluator_version differs: baseline {bm.evaluator_version}, "
                f"candidate {cm.evaluator_version}",
            )
        )

    base = {c.case_id: c for c in baseline.cases}
    cand = {c.case_id: c for c in candidate.cases}
    for label, ids in (
        ("missing from candidate", base.keys() - cand.keys()),
        ("missing from baseline", cand.keys() - base.keys()),
    ):
        if ids:
            out.append(
                _diag(
                    rules.require_same_cases,
                    "case_mismatch",
                    f"{len(ids)} case(s) {label}: {_ids(ids)}",
                )
            )

    matched = sorted(base.keys() & cand.keys())
    compared = sum(base[i].status == cand[i].status == "ok" for i in matched)
    if not compared:
        out.append(_diag(True, "no_comparable_cases", "no case is `ok` in both runs"))
    elif rules.min_compared_cases is not None:
        if compared < rules.min_compared_cases:
            out.append(
                _diag(
                    True,
                    "too_few_cases",
                    f"{compared} case(s) compared; policy requires at least "
                    f"{rules.min_compared_cases}",
                )
            )
    elif compared < SMALL_SAMPLE:
        out.append(
            _diag(
                False,
                "small_sample",
                f"only {compared} case(s) compared; mean differences may be noise. "
                "Set comparison.min_compared_cases to enforce a minimum",
            )
        )

    # Every compared metric must exist wherever the case ran; a gap would bias the mean.
    for name, rule in sorted(policy.metrics.items()):
        for label, cases in (("baseline", base), ("candidate", cand)):
            missing = [
                i for i in matched if cases[i].status == "ok" and cases[i].value(rule.path) is None
            ]
            if missing:
                out.append(
                    _diag(
                        True,
                        "metric_missing",
                        f"{label}: metric '{name}' ({rule.path}) missing in "
                        f"{len(missing)} ok case(s): {_ids(missing)}",
                    )
                )

    # A check that vanishes in the candidate could hide a new failure.
    for check in sorted(set(policy.checks.required)):
        missing = [
            i for i in sorted(cand) if cand[i].status == "ok" and check not in cand[i].checks
        ]
        if missing:
            out.append(
                _diag(
                    True,
                    "check_missing",
                    f"candidate: required check '{check}' missing in {len(missing)} ok case(s): "
                    f"{_ids(missing)}",
                )
            )
    dropped = sorted(
        (i, check)
        for i in matched
        if base[i].status == cand[i].status == "ok"
        for check in base[i].checks.keys() - cand[i].checks.keys() - set(policy.checks.required)
    )
    if dropped:
        out.append(
            _diag(
                True,
                "check_missing",
                f"candidate: {len(dropped)} check result(s) present in baseline but missing: "
                + _ids(f"{i}/{check}" for i, check in dropped),
            )
        )
    return out
