# EvalGate

Local-first AI regression testing and release decision engine.

EvalGate compares a baseline evaluation run against a candidate, finds what improved and what regressed, applies your release policy, and returns an explainable **PASS / FAIL / INVALID** with CI-friendly exit codes (`0` / `1` / `2`). Fully offline, no LLM API key needed.

> Status: early development (Phase 1, core comparison engine). See [docs/SPEC.md](docs/SPEC.md).

## Usage

Each run is a directory with `run.json` (manifest) and `cases.jsonl` (one case per line). See [examples/quickstart](examples/quickstart) and the spec's data model (§5) for the format.

```bash
evalgate init                      # writes a documented evalgate.yaml to edit
evalgate validate examples/quickstart/candidate
evalgate compare examples/quickstart/baseline examples/quickstart/candidate \
  --policy examples/quickstart/evalgate.yaml --json report.json --report report.html
```

The quickstart candidate improves mean quality but newly fails `safety_valid` on one case, so the decision is **FAIL** (exit 1).

`--report` writes a single standalone HTML file with no scripts and no external assets. Open it locally or attach it to a CI artifact. Like the JSON report, it contains scores, checks and case IDs, and never raw prompts or outputs.

Policy notes:
- Every metric declares `direction: maximize | minimize`.
- `max_mean_regression_pct` is undefined when the baseline mean is 0; add `max_mean_regression_abs` or the result is INVALID.
- Cases with `status: "error"` are excluded from means and count against `max_execution_errors` (default 0).
- Missing/duplicate cases, missing metrics or checks, and dataset/evaluator mismatches give INVALID (exit 2).

## Python SDK

```python
from evalgate import EvalRun, compare

with EvalRun(
    "candidate", dataset="support-suite", dataset_version="v3", evaluator_version="rubric-v2"
) as run:  # writes evals/candidate/
    for case in dataset:
        try:
            output = execute_agent(case)
        except Exception as exc:
            run.record_error(case["id"], repr(exc))  # never invent scores
            continue
        run.record(
            case["id"],
            scores={"quality": judge(output)},
            checks={"safety_valid": is_safe(output)},
            metrics={"latency_ms": 1234},
        )

report = compare("evals/baseline", "evals/candidate", policy="evalgate.yaml")
print(report.summary())
report.to_json("report.json")
report.to_html("report.html")
```

- Values are validated as you record them. Duplicate `case_id`s raise.
- `run.json` is written only when the `with` block exits cleanly. An interrupted run never loads as complete.
- `EvalRun` refuses to overwrite an existing run unless you pass `overwrite=True`.
- The SDK's `compare` is the same code path as `evalgate compare`, and both produce identical reports.

Runnable demo: `python examples/sdk_quickstart.py`.

## Development

```bash
uv sync
uv run pytest
uv run evalgate --help
git config core.hooksPath .githooks   # once per clone
```

There is no hosted CI. The `pre-push` hook runs ruff, the ruff format check, mypy and pytest, and blocks the push on any failure.

## License

MIT
