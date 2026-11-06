# EvalGate

**Local-first regression testing and release decisions for AI applications.**

You changed a prompt, model, tool or retrieval step. Your eval pipeline produced scores for the old version and the new one. EvalGate answers three questions:

1. **What changed?**
2. **What got worse?**
3. **Is this version acceptable to release?**

It compares a *baseline* run against a *candidate* run case by case and applies your release policy. It returns an explainable **PASS / FAIL / INVALID**, with exit codes `0 / 1 / 2` for CI. It runs fully offline, with no LLM API key, account or database.

```text
$ evalgate compare evals/baseline evals/candidate --policy evalgate.yaml
baseline-v1 -> candidate-v2  dataset regression-suite@v3  evaluator rubric-v2
cases: 4 matched, 4 compared, 0 baseline errors, 0 candidate errors
metric   direction  baseline  candidate     delta  delta %
quality  maximize        0.8      0.865    +0.065   +8.12%
...
FAIL checks.required.safety_valid: required check 'safety_valid' failed in 1 case(s): abuse-001/safety_valid
decision: FAIL
```

Average quality went up, but one case started failing a safety check, so the candidate does not ship.

## Install

```bash
pip install evalgate-ai
```

The package is published as `evalgate-ai`. It imports as `evalgate`, and the command is `evalgate`. Requires Python 3.11 or newer.

## Quickstart

**1. Write each run as a directory.** Use `run.json` for identity and `cases.jsonl` with one JSON object per test case:

```json
{"schema_version": "1.0", "run_id": "candidate-v2", "dataset_id": "regression-suite",
 "dataset_version": "v3", "evaluator_version": "rubric-v2"}
```

```json
{"case_id": "refund-001", "status": "ok", "scores": {"quality": 0.91},
 "checks": {"safety_valid": true}, "metrics": {"latency_ms": 3000, "cost_usd": 0.012},
 "metadata": {"category": "billing"}}
```

If the pipeline failed on a case, record `"status": "error"` instead of inventing scores. Or let the Python SDK below write these files for you.

**2. Create a policy:**

```bash
evalgate init          # writes a commented evalgate.yaml
```

```yaml
version: 1
metrics:
  quality:
    path: scores.quality
    direction: maximize          # explicit: EvalGate never assumes higher is better
    max_mean_regression_abs: 0.02
  latency:
    path: metrics.latency_ms
    direction: minimize
    max_mean_regression_pct: 10
checks:
  required: [safety_valid]       # must pass in every candidate case
  max_new_failures: 0            # nothing that passed before may fail now
```

**3. Compare:**

```bash
evalgate validate evals/candidate
evalgate compare evals/baseline evals/candidate --policy evalgate.yaml \
  --json report.json --report report.html
```

- `--report` writes one standalone HTML file: no scripts and no external assets.
- `--json` writes a stable, documented report: see [report format](https://github.com/josep-audenis/evalgate/blob/main/docs/report-format.md).
- Neither report contains raw prompts or outputs.

### Repeated runs

LLM outputs vary from run to run. To measure that noise, evaluate each case several times: add `"repeat": 0`, `1`, … to each line of `cases.jsonl`, set `"schema_version": "1.1"`, and optionally declare `"repeats": 3` in `run.json`. In the SDK, use `EvalRun(..., repeats=3)` and `run.record(case_id, repeat=i, ...)`.

- **How repeats are combined per case:** metrics are averaged, a check passes only if it passes in every repeat, and any errored repeat makes the case an error. Means, intervals and rules then run on cases, so 3 repeats × 100 cases is still 100 cases, never 300.
- **Fail closed:** a missing or duplicated repeat makes the result INVALID.
- **Report:** shows how much each metric varies between repeats within a case, and each check's pass rate.

### Importing from other tools

Already have results from another tool as CSV, JSON or JSONL? Describe the columns once in a mapping file and convert:

```bash
evalgate import results.csv --mapping mapping.yaml --out evals/candidate --run-id candidate-v2
```

Bad records are all reported, and nothing is written unless every record converts. Unmapped columns, such as prompts and outputs, are never copied. See [importing](https://github.com/josep-audenis/evalgate/blob/main/docs/importing.md).

Using promptfoo? Import its `-o results.json` directly, no mapping needed:

```bash
evalgate import results.json --from promptfoo --out evals/candidate \
  --dataset support-suite --dataset-version v3 --evaluator-version pf-asserts-1
```

See [promptfoo](https://github.com/josep-audenis/evalgate/blob/main/docs/promptfoo.md). DeepEval test run files work the same way with `--from deepeval`: see [DeepEval](https://github.com/josep-audenis/evalgate/blob/main/docs/deepeval.md). For Langfuse experiments, write the run from your experiment script: see [Langfuse](https://github.com/josep-audenis/evalgate/blob/main/docs/langfuse.md).

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

- **Validation:** values are checked as you record them. Duplicate `case_id`s raise an error.
- **Crash safety:** `run.json` is written only when the `with` block exits cleanly, so an interrupted run never loads as complete.
- **Overwrites:** `EvalRun` won't replace an existing run unless you pass `overwrite=True`. Even then, the old run is replaced only when the new one finishes cleanly.
- **Same results as the CLI:** the SDK's `compare` is the same code path as `evalgate compare`, and the reports are identical.

## How decisions are made

- **INVALID (exit 2):** a trustworthy decision is impossible. Causes:
  - missing or duplicate cases
  - a dataset or evaluator version mismatch
  - missing metrics or checks
  - malformed files
  - a percent threshold on a metric whose baseline mean is 0

  Invalid input never produces a PASS.
- **FAIL (exit 1):** the comparison is valid, but at least one policy rule is violated.
- **PASS (exit 0):** the comparison is valid and every rule holds.

Further rules:
- Means are computed over cases that ran successfully in both runs.
- With fewer than 30 such cases, the report warns that differences may be noise. Set `comparison.min_compared_cases` to turn a too-small sample into INVALID.
- Cases with `status: "error"` count against `max_execution_errors` (default 0) and never count as successes.
- Every metric declares its direction and at least one threshold.
- The policy is strict. Unknown or duplicate keys, `true` where a number is expected, and a policy with no rules are all rejected.
- Each metric's mean change comes with a confidence interval (95% by default, `statistics.confidence`). A rule whose interval spans its threshold is reported as *inconclusive*. With `statistics: {require_conclusive: true}`, an inconclusive pass becomes a FAIL. Statistics can tighten a decision, never loosen it.
- Each check also reports how many cases newly fail and newly pass, with a one-sided exact McNemar p-value. With `checks.fail_on_significant_regression: true`, a significant increase in failures is a FAIL. Combine it with another rule, because with only a few flips the test can never reach significance. That's useful when checks flip noisily and `max_new_failures: 0` is too strict.
- A metric can also set `max_category_regression_abs` / `_pct`: the same limit applied to each `metadata.category` on its own, so a regression confined to one category fails even when the overall mean improves.
- How the intervals, tests and limits work, and what they can't tell you: [statistics](https://github.com/josep-audenis/evalgate/blob/main/docs/statistics.md).
- There is no weighted overall score. Each rule passes or fails on its own, so one regression can't hide behind an improvement elsewhere.

## Use in CI

Exit codes make EvalGate a drop-in CI step. See [CI integration](https://github.com/josep-audenis/evalgate/blob/main/docs/ci.md) for a GitHub Actions example.

## Examples

[examples/](https://github.com/josep-audenis/evalgate/tree/main/examples) has runnable scenarios:
- a safety regression hidden by better averages
- a quality gain that blows the cost and latency budget
- an SDK eval loop with a timed-out case

## Status and scope

Version 0.5.0 (alpha). See the [changelog](https://github.com/josep-audenis/evalgate/blob/main/CHANGELOG.md). EvalGate compares results your pipeline already produced. It does **not** run your application, call LLM judges, or host datasets.

Not yet included:
- dedicated adapters for tools other than promptfoo and DeepEval; the generic importer covers their CSV/JSON exports

Planned work is in the [spec](https://github.com/josep-audenis/evalgate/blob/main/docs/SPEC.md).

## Contributing

See [CONTRIBUTING.md](https://github.com/josep-audenis/evalgate/blob/main/CONTRIBUTING.md).

## License

MIT
