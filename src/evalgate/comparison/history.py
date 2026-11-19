"""Pure summaries for the local history store (docs/design/phase-7.md §2). No I/O.

They read scores, metrics, checks and manifests only, so no output, prompt or case data can
reach a history entry.
"""

import math
from statistics import fmean
from typing import Any, Literal

from evalgate.comparison.repeats import collapse
from evalgate.models import Report, Run, RunManifest
from evalgate.models.history import CompareEntry, HistoryEntry, Identity, RunEntry


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
    version = 2
    # An INVALID report (e.g. a policy diagnostic) can still carry a comparison: never summarise it.
    if report.comparison is not None and report.status != "INVALID":
        # Two policy metrics on one path have the same means; the first one's direction is kept.
        for m in report.comparison.metrics:
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
        groups = report.comparison.failure_groups
        if groups is not None:  # 0.12+: exactly the compared cases
            pairs = {(case, g.check) for g in groups.by_check for case in g.cases}
        else:  # a report from before 0.12: the 0.10 approximation, recorded as a v1 entry
            skip = set(report.comparison.baseline_errors)
            pairs = {
                (c.case_id, c.check)
                for c in report.comparison.candidate_failing_checks
                if c.case_id not in skip
            }
            version = 1
        failing = [{"case_id": case, "check": check} for case, check in sorted(pairs)]
    return {
        "history_version": version,
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
            raise ValueError(f"{key}: mean is not finite") from None
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
        "case_ids": sorted(c.case_id for c in ok),
    }


def _matches_dataset(identity: Identity, dataset: str | None) -> bool:
    # Exact match on the id or on "id@version"; no parsing, so an id may contain "@".
    return dataset is None or dataset in (
        identity.dataset_id,
        f"{identity.dataset_id}@{identity.dataset_version}",
    )


def trend(
    entries: list[HistoryEntry],
    path: str,
    *,
    kind: Literal["run", "compare"] = "run",
    dataset: str | None = None,
    application: str | None = None,
    last: int | None = None,
) -> dict[str, Any]:
    """One metric path across entries of one kind, in id order. `--last` applies after
    filtering and skipping. Kinds never mix: a compare mean covers compared cases only."""
    points: list[dict[str, Any]] = []
    datasets: list[tuple[str, str]] = []  # parallel to points; tuples, so ids may hold '@'
    skipped = 0
    for entry in entries:
        if entry.kind != kind:
            continue
        identity = entry.run if isinstance(entry, RunEntry) else entry.candidate
        if identity is None:  # an INVALID compare whose candidate didn't load: no value
            skipped += dataset is None and application is None  # can't match a filter
            continue
        if not _matches_dataset(identity, dataset):
            continue
        if application is not None and identity.application != application:
            continue
        if isinstance(entry, RunEntry):
            stat = entry.means.get(path)
            value = None if stat is None else (stat.value, stat.cases)
        else:
            assert isinstance(entry, CompareEntry)
            row = next((r for r in entry.metrics if r.path == path), None)
            value = None if row is None else (row.candidate_mean, row.cases)
        if value is None:
            skipped += 1
            continue
        datasets.append((identity.dataset_id, identity.dataset_version))
        points.append(
            {
                "id": entry.id,
                "label": entry.label,
                "recorded_at": entry.recorded_at,
                "application_version": identity.application_version,
                "dataset": f"{identity.dataset_id}@{identity.dataset_version}",
                "value": value[0],
                "cases": value[1],
            }
        )
    if last is not None:
        points = points[-last:] if last else []
        datasets = datasets[-last:] if last else []
    return {
        "history_version": 1,
        "path": path,
        "kind": kind,
        "points": points,
        "skipped": skipped,
        "mixed_datasets": len(set(datasets)) > 1,
    }
