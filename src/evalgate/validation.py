"""Pure validation of runs and run pairs. Any `error` diagnostic makes the comparison INVALID."""

from collections import Counter
from collections.abc import Iterable

from evalgate.models import Diagnostic, Policy, Run

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
    counts = Counter(c.case_id for c in run.cases)
    dupes = [case_id for case_id, n in counts.items() if n > 1]
    if dupes:
        return [_diag(True, "duplicate_case_id", f"{label}: duplicate case_id: {_ids(dupes)}")]
    return []


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
