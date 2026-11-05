"""Import a promptfoo results file (`promptfoo eval -o results.json`) as an EvalGate run.

Supports promptfoo's results format version 3 (checked against promptfoo 0.125). Each
result row becomes one case (or one repeat of a case with `--repeat`):

- case_id: `testCase.description` by default, or `vars.<name>` / `metadata.<name>`
- status: `error` when promptfoo reports an error (failureReason 2) or its grader failed
- scores: `score` (promptfoo's overall score) and every named metric (`namedScores`)
- checks: `pass` (the test passed) and, per named metric, whether all its assertions passed
- metrics: `latency_ms`, `cost`, `tokens` (left out for cached responses)
- metadata: scalar values of `testCase.metadata` (so `category` works with category limits)

Prompts, vars and outputs are never copied. Fail closed: anything ambiguous is an error.
"""

import json
from collections import Counter
from math import isfinite
from pathlib import Path
from typing import Any

from evalgate.io.importer import ImportSummary, _unique_keys, write_rows
from evalgate.io.reader import InputError, _error, _parse, _read_text
from evalgate.models import Diagnostic
from evalgate.models.mapping import ImportManifest

_ERROR = 2  # promptfoo ResultFailureReason.ERROR


def _fail(code: str, message: str) -> InputError:
    return InputError([_error(code, message)])


def _summary(source: Path) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(_read_text(source), object_pairs_hook=_unique_keys)
    except ValueError as exc:
        raise _fail("import_parse", f"{source}: cannot parse as JSON: {exc}") from exc
    # `-o results.json` wraps the summary: {evalId, results: {version, results: [...]}, ...}
    if isinstance(data, dict) and isinstance(data.get("results"), dict):
        eval_id = data.get("evalId")
        data = data["results"]
    else:
        eval_id = None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise _fail("import_parse", f"{source}: not a promptfoo results file")
    version = data.get("version")
    if version != 3 or isinstance(version, bool):
        raise _fail(
            "import_format",
            f"{source}: promptfoo results version {version!r} is not supported (need 3); "
            "re-export with a current promptfoo",
        )
    return data, eval_id if isinstance(eval_id, str) and eval_id else None


def _target(row: dict[str, Any]) -> tuple[str, str]:
    provider = row.get("provider") or {}
    prompt = row.get("prompt") or {}
    return (
        str(provider.get("label") or provider.get("id") or "?"),
        str(prompt.get("label") or row.get("promptId") or "?"),
    )


def _select(
    source: Path, rows: list[dict[str, Any]], provider: str | None, prompt: str | None
) -> list[dict[str, Any]]:
    """Keep one provider x prompt: an EvalGate run is one version of one system."""

    def matches(row: dict[str, Any]) -> bool:
        p, q = row.get("provider") or {}, row.get("prompt") or {}
        return (provider is None or provider in (p.get("label"), p.get("id"))) and (
            prompt is None or prompt in (q.get("label"), row.get("promptId"))
        )

    kept = [r for r in rows if matches(r)]
    targets = sorted({_target(r) for r in kept})
    if len(targets) == 1:
        return kept
    found = "; ".join(
        f"provider {p!r} / prompt {q!r}" for p, q in sorted({_target(r) for r in rows})
    )
    reason = "nothing matches" if not targets else "it holds several provider/prompt combinations"
    raise _fail(
        "import_select",
        f"{source}: {reason}; pick one with --provider and/or --prompt. Found: {found}",
    )


def _case_id(row: dict[str, Any], field: str) -> str | None:
    test = row.get("testCase") or {}
    if field == "description":
        value = test.get("description")
    else:
        section, _, name = field.partition(".")
        value = (test.get(section) or {}).get(name)
    if isinstance(value, bool) or not isinstance(value, str | int) or not str(value).strip():
        return None
    return str(value).strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _grader_failed(grading: dict[str, Any]) -> bool:
    parts = [grading, *(grading.get("componentResults") or [])]
    return any((p.get("metadata") or {}).get("graderError") for p in parts if isinstance(p, dict))


def _convert(row: dict[str, Any], case_field: str) -> dict[str, Any]:
    """One promptfoo result row -> write_rows() row. Raises ValueError listing every issue."""
    problems: list[str] = []
    case_id = _case_id(row, case_field)
    if case_id is None:
        hint = "give every test a description" if case_field == "description" else "check the field"
        problems.append(f"no usable case id in testCase.{case_field} ({hint}, or use --case-id)")
    test = row.get("testCase") or {}
    metadata = {
        k: v
        for k, v in sorted((test.get("metadata") or {}).items())
        if isinstance(v, str | int | float | bool) and not k.startswith("_")
    }
    out: dict[str, Any] = {"case_id": case_id, "metadata": metadata}
    if "repeatIndex" in row:
        repeat = row["repeatIndex"]
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0:
            problems.append(f"repeatIndex must be a non-negative integer, got {repeat!r}")
        out["repeat"] = repeat

    grading = row.get("gradingResult") or {}
    if row.get("failureReason") == _ERROR or _grader_failed(grading):
        # promptfoo errors carry a stack trace with local paths: keep the first line only.
        message = str(row.get("error") or "grader error").strip().splitlines()
        out |= {"status": "error", "error": message[0] if message else "error"}
    else:
        success = row.get("success")
        if not isinstance(success, bool):
            problems.append(f"success must be true/false, got {success!r}")
        named = row.get("namedScores") or {}
        scores = {"score": _number(row.get("score"))} | {
            str(k): _number(v) for k, v in sorted(named.items())
        }
        if "score" in named:
            problems.append(
                "a named metric is called 'score', which clashes with the overall score"
            )
        bad = sorted(k for k, v in scores.items() if v is None)
        if bad:
            problems.append(f"scores must be finite numbers: {', '.join(bad)}")
        checks: dict[str, bool] = {"pass": success is True}
        for part in grading.get("componentResults") or []:
            metric = ((part or {}).get("assertion") or {}).get("metric")
            if isinstance(metric, str) and metric:
                if metric == "pass":
                    problems.append("a named metric is called 'pass', which clashes with the check")
                checks[metric] = checks.get(metric, True) and part.get("pass") is True
        cached = (row.get("response") or {}).get("cached") is True
        tokens = (row.get("tokenUsage") or {}).get("total")
        raw = {"latency_ms": row.get("latencyMs"), "cost": row.get("cost"), "tokens": tokens}
        # Cached responses report no real latency or cost: leave them out, never record 0.
        metrics = {} if cached else {k: n for k, v in raw.items() if (n := _number(v)) is not None}
        out |= {"status": "ok", "scores": scores, "checks": checks, "metrics": metrics}
    if problems:
        raise ValueError("; ".join(problems))
    return out


def import_promptfoo(
    source: str | Path,
    out: str | Path,
    *,
    dataset: str,
    dataset_version: str,
    evaluator_version: str,
    run_id: str | None = None,
    case_id: str = "description",
    provider: str | None = None,
    prompt: str | None = None,
    overwrite: bool = False,
) -> ImportSummary:
    """Write a promptfoo results file as an EvalGate run at `out`. Raises InputError on bad input.

    `case_id` is `description`, `vars.<name>` or `metadata.<name>`. `run_id` defaults to the
    promptfoo evalId. With several providers or prompts, pick one with `provider` / `prompt`
    (label or id).
    """
    source, out = Path(source), Path(out)
    if case_id != "description" and not (
        case_id.partition(".")[0] in ("vars", "metadata") and case_id.partition(".")[2]
    ):
        raise _fail(
            "import_case_id", "--case-id must be description, vars.<name> or metadata.<name>"
        )
    manifest = _parse(
        ImportManifest,
        {
            "dataset_id": dataset,
            "dataset_version": dataset_version,
            "evaluator_version": evaluator_version,
        },
        "manifest",
    )
    summary, eval_id = _summary(source)
    run_id = run_id or eval_id
    if not run_id:
        raise _fail("import_run_id", f"{source}: no evalId in the file; pass --run-id")

    raw_rows = summary["results"]
    if not raw_rows:
        raise _fail("no_cases", f"{source}: contains no results")
    if not all(isinstance(r, dict) for r in raw_rows):
        raise _fail("import_parse", f"{source}: every result must be an object")
    selected = _select(source, raw_rows, provider, prompt)
    # promptfoo writes rows in completion order: report problems in test order.
    selected.sort(key=lambda r: r["testIdx"] if isinstance(r.get("testIdx"), int) else -1)
    rows, diagnostics = [], []
    for row in selected:
        where = f"test {row.get('testIdx')!r}" + (
            f" repeat {row['repeatIndex']!r}" if "repeatIndex" in row else ""
        )
        try:
            rows.append(_convert(row, case_id))
        except ValueError as exc:
            diagnostics.append(_error("import_record", f"{source}: {where}: {exc}"))
    _duplicates(source, rows, case_id, diagnostics)
    if diagnostics:
        raise InputError(diagnostics)
    rows.sort(key=lambda r: (r["case_id"], r.get("repeat", 0)))  # deterministic run file
    return write_rows(source, rows, out, run_id, manifest, overwrite=overwrite, ignored_fields=[])


def _duplicates(
    source: Path, rows: list[dict[str, Any]], field: str, diagnostics: list[Diagnostic]
) -> None:
    counts = Counter((r["case_id"], r.get("repeat")) for r in rows)
    dupes = sorted({case for (case, _), n in counts.items() if n > 1})
    if dupes:
        diagnostics.append(
            _error(
                "duplicate_case_id",
                f"{source}: several tests share the case id from testCase.{field}: "
                f"{', '.join(map(repr, dupes))}; make them unique or use --case-id",
            )
        )
