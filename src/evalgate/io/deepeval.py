"""Import a DeepEval test run file as an EvalGate run.

Reads the JSON DeepEval writes with `DisplayConfig(results_folder=...)` /
`DEEPEVAL_RESULTS_FOLDER` (`test_run_<timestamp>.json`) or `.deepeval/.latest_run_full.json`
(checked against deepeval 4.3). Each test case (single-turn or conversational) becomes a case:

- case_id: the test case `name`, or `metadata.<key>`. DeepEval's default names
  (`test_case_<n>` and similar, from the position) are rejected: they change when cases move.
- status: `error` when any metric errored (`error` set, or a threshold without a score)
- scores: each metric's score, under the metric name
- checks: `pass` (the test case passed) and, per metric with a threshold, whether it passed
- metrics: `latency_ms` (from `completionTime`), `cost` (`tokenCost`), `tokens`
- metadata: scalar values of the test case `metadata`

Inputs, outputs, contexts, turns, reasons and error messages are never copied.
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from evalgate.io.importer import ImportSummary, _unique_keys, write_rows
from evalgate.io.promptfoo import _fail, _number, _scalars
from evalgate.io.reader import InputError, _error, _parse, _read_text
from evalgate.models.mapping import ImportManifest

# Position-based names DeepEval assigns to unnamed test cases: `test_case_<n>`,
# `conversational_test_case_<n>`, and pytest's parametrize ids under `deepeval test run`
# (`test_bot[test_case0]`, or `test_bot[test_case0-gpt]` with a second parametrize).
_DEFAULT_NAME = re.compile(
    r"(conversational_)?test_case_\d+|.*\[(?:[^\]]*-)?test_case\d+(?:-[^\]]*)?\]"
)


def _test_run(source: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read_text(source), object_pairs_hook=_unique_keys)
    except ValueError as exc:
        raise _fail("import_parse", f"{source}: cannot parse as JSON: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("testRunData"), dict):
        data = data["testRunData"]
    lists = ("testCases", "conversationalTestCases")
    if not isinstance(data, dict) or not any(isinstance(data.get(k), list) for k in lists):
        raise _fail(
            "import_parse",
            f"{source}: not a DeepEval test run file (expected testCases); use the "
            "test_run_*.json from the results folder",
        )
    return data


def _case_id(case: dict[str, Any], field: str) -> str | None:
    if field == "name":
        value = case.get("name")
        if isinstance(value, str) and _DEFAULT_NAME.fullmatch(value.strip()):
            return None
    else:
        value = (case.get("metadata") or {}).get(field.removeprefix("metadata."))
    if isinstance(value, bool) or not isinstance(value, str | int) or not str(value).strip():
        return None
    return str(value).strip()


def _convert(case: dict[str, Any], field: str) -> dict[str, Any]:
    """One DeepEval test case -> write_rows() row. Raises ValueError listing every issue."""
    problems: list[str] = []
    case_id = _case_id(case, field)
    if case_id is None:
        problems.append(
            f"no usable case id in {field} (give each test case a name of its own, not a "
            "position-based default like test_case_<n> or test_x[test_case0], "
            "or use --case-id metadata.<key>)"
            if field == "name"
            else f"no usable case id in {field} (every test case needs a non-empty value there)"
        )
    out: dict[str, Any] = {"case_id": case_id, "metadata": _scalars(case.get("metadata") or {})}

    metrics_data = case.get("metricsData") or []
    if not isinstance(metrics_data, list) or not all(isinstance(m, dict) for m in metrics_data):
        raise ValueError("metricsData must be a list of objects")
    names = [m.get("name") for m in metrics_data]
    bad = sorted({repr(n) for n in names if not isinstance(n, str) or not n or "." in n})
    if bad:
        problems.append(f"metric names must be text without dots: {', '.join(bad)}")
    dupes = sorted(n for n, k in Counter(n for n in names if isinstance(n, str)).items() if k > 1)
    if dupes:
        problems.append(f"metric reported twice: {', '.join(dupes)}")
    clash = sorted({"pass"} & {n for n in names if isinstance(n, str)})
    if clash:
        problems.append("a metric is called 'pass', which clashes with the overall check")

    # Errored: `error` set (ignore_errors), or a thresholded metric without a score, which is
    # how `deepeval test run` records a metric that raised.
    if any(
        m.get("error") or (m.get("threshold") is not None and "score" not in m)
        for m in metrics_data
    ):
        # Fixed text: DeepEval error messages can quote inputs, outputs or local paths.
        out |= {"status": "error", "error": "deepeval metric error; details not imported"}
    else:
        scores: dict[str, float] = {}
        checks: dict[str, bool] = {}
        for m in metrics_data:
            name = str(m.get("name"))
            if "score" in m:
                if (score := _number(m["score"])) is None:
                    problems.append(f"{name}: score must be a finite number, got {m['score']!r}")
                else:
                    scores[name] = score
            # Flaky metrics don't decide DeepEval's pass/fail either: score only.
            if m.get("threshold") is not None and not m.get("flaky"):
                if not isinstance(m.get("success"), bool):
                    problems.append(f"{name}: success must be true/false, got {m.get('success')!r}")
                checks[name] = m.get("success") is True
        if "success" in case:
            if not isinstance(case["success"], bool):
                problems.append(f"success must be true/false, got {case['success']!r}")
            checks["pass"] = case["success"] is True
        latency = _number(case.get("completionTime"))
        counts = [_number(case.get(k)) for k in ("inputTokenCount", "outputTokenCount")]
        raw = {
            "latency_ms": None if latency is None else latency * 1000,
            "cost": _number(case.get("tokenCost")),
            "tokens": None if None in counts else sum(c for c in counts if c is not None),
        }
        metrics = {k: v for k, v in raw.items() if v is not None}
        negative = sorted(k for k, v in metrics.items() if v < 0)
        if negative:
            problems.append(f"must not be negative: {', '.join(negative)}")
        out |= {"status": "ok", "scores": scores, "checks": checks, "metrics": metrics}
    if problems:
        raise ValueError("; ".join(problems))
    return out


def import_deepeval(
    source: str | Path,
    out: str | Path,
    *,
    dataset: str,
    dataset_version: str,
    evaluator_version: str,
    run_id: str | None = None,
    case_id: str = "name",
    overwrite: bool = False,
) -> ImportSummary:
    """Write a DeepEval test run file as an EvalGate run at `out`. Raises InputError on bad input.

    `case_id` is `name` or `metadata.<key>`. `run_id` defaults to the run's `identifier`, else
    the file name (`test_run_<timestamp>`).
    """
    source, out = Path(source), Path(out)
    if case_id != "name" and not (case_id.startswith("metadata.") and case_id[9:]):
        raise _fail("import_case_id", "--case-id must be name or metadata.<key> for DeepEval")
    manifest = _parse(
        ImportManifest,
        {
            "dataset_id": dataset,
            "dataset_version": dataset_version,
            "evaluator_version": evaluator_version,
        },
        "manifest",
    )
    run = _test_run(source)
    identifier = run.get("identifier")
    run_id = run_id or (identifier if isinstance(identifier, str) and identifier else source.stem)

    cases: list[tuple[str, Any]] = []
    for key in ("testCases", "conversationalTestCases"):
        value = run.get(key) or []
        if not isinstance(value, list):
            raise _fail("import_parse", f"{source}: {key} must be a list")
        cases += [(f"{key}[{i}]", c) for i, c in enumerate(value)]
    if not cases:
        raise _fail("no_cases", f"{source}: contains no test cases")

    rows, diagnostics = [], []
    for where, case in cases:
        try:
            if not isinstance(case, dict):
                raise ValueError("must be an object")
            rows.append(_convert(case, case_id))
        except ValueError as exc:
            diagnostics.append(_error("import_record", f"{source}: {where}: {exc}"))
        except (AttributeError, TypeError):  # a field of an unexpected type
            diagnostics.append(_error("import_record", f"{source}: {where}: unexpected structure"))
    dupes = sorted(n for n, k in Counter(r["case_id"] for r in rows).items() if k > 1)
    if dupes:
        diagnostics.append(
            _error(
                "duplicate_case_id",
                f"{source}: several test cases share the case id from {case_id}: "
                f"{', '.join(map(repr, dupes))}; make them unique or use --case-id",
            )
        )
    if diagnostics:
        raise InputError(diagnostics)
    rows.sort(key=lambda r: r["case_id"])  # deterministic run file
    return write_rows(source, rows, out, run_id, manifest, overwrite=overwrite, ignored_fields=[])
