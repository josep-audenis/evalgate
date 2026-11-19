"""Pure summaries for the local history store (docs/design/phase-7.md §2). No I/O.

They read scores, metrics, checks and manifests only, so no output, prompt or case data can
reach a history entry.
"""

import math
from statistics import fmean
from typing import Any

from evalgate.comparison.repeats import collapse
from evalgate.models import Report, Run, RunManifest


def _identity(manifest: RunManifest | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    return manifest.model_dump(
        include={"run_id", "application", "application_version", "dataset_id", "dataset_version"}
    )


def summarize_report(report: Report) -> dict[str, Any]:
    """The `compare` entry fields of a report (without id, time, label and report hash)."""
    rows: dict[str, dict[str, Any]] = {}
    failing: list[dict[str, str]] = []
    if report.comparison is not None:
        for m in report.comparison.metrics:  # two policy metrics on one path: same numbers
            rows.setdefault(
                m.path,
                {
                    "path": m.path,
                    "direction": m.direction,
                    "baseline_mean": m.baseline_mean,
                    "candidate_mean": m.candidate_mean,
                    "delta_abs": m.delta_abs,
                    "cases": m.cases,
                },
            )
        failing = [
            {"case_id": c.case_id, "check": c.check}
            for c in sorted(
                report.comparison.candidate_failing_checks, key=lambda c: (c.case_id, c.check)
            )
        ]
    return {
        "kind": "compare",
        "status": report.status,
        "baseline": _identity(report.baseline),
        "candidate": _identity(report.candidate),
        "metrics": [rows[p] for p in sorted(rows)],
        "failing_checks": failing,
    }


def _stats(values: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    out = {}
    for key in sorted(values):
        try:
            value = fmean(values[key])
        except OverflowError:  # fsum over finite values can only overflow
            value = math.inf
        if not math.isfinite(value):
            raise ValueError(f"{key}: mean is not finite")
        out[key] = {"value": value, "cases": len(values[key])}
    return out


def summarize_run(run: Run) -> dict[str, Any]:
    """The `run` entry fields of a valid run, repeats collapsed first. Raises ValueError when a
    mean isn't finite (the caller reports `history_record`)."""
    try:
        collapsed = collapse(run)
    except OverflowError:
        raise ValueError("metric values too large to average") from None
    means: dict[str, list[float]] = {}
    checks: dict[str, list[float]] = {}
    failing = []
    ok = [c for c in collapsed.cases if c.status == "ok"]
    for case in ok:
        for section, values in (("scores", case.scores), ("metrics", case.metrics)):
            for name, value in values.items():
                means.setdefault(f"{section}.{name}", []).append(value)
        for name, passed in case.checks.items():
            checks.setdefault(name, []).append(1.0 if passed else 0.0)
            if not passed:
                failing.append({"case_id": case.case_id, "check": name})
    return {
        "kind": "run",
        "run": _identity(run.manifest),
        "cases": len(collapsed.cases),
        "errors": len(collapsed.cases) - len(ok),
        "means": _stats(means),
        "checks": _stats(checks),
        "failing_checks": sorted(failing, key=lambda f: (f["case_id"], f["check"])),
    }
