# Importing promptfoo results

`evalgate import --from promptfoo` turns the JSON file written by `promptfoo eval -o results.json` into an EvalGate run. No mapping file is needed. Export one file for the baseline version and one for the candidate, import both, then compare.

```bash
promptfoo eval -o baseline.json        # on the baseline version
promptfoo eval -o candidate.json       # on the candidate version

evalgate import baseline.json --from promptfoo --out evals/baseline   --dataset support-suite --dataset-version v3 --evaluator-version pf-asserts-1
evalgate import candidate.json --from promptfoo --out evals/candidate   --dataset support-suite --dataset-version v3 --evaluator-version pf-asserts-1
evalgate compare evals/baseline evals/candidate --policy evalgate.yaml
```

```python
from evalgate import import_promptfoo

import_promptfoo(
    "candidate.json", "evals/candidate",
    dataset="support-suite", dataset_version="v3", evaluator_version="pf-asserts-1",
)
```

promptfoo's file doesn't say which dataset version or evaluator produced it, so you pass those. Change `--evaluator-version` whenever you change your assertions or grader, so EvalGate refuses to compare results graded differently. `--run-id` defaults to promptfoo's `evalId`.

Supported: promptfoo results format version 3 (current promptfoo; checked against 0.125). Other versions are rejected with `import_format`.

## What becomes what

Each promptfoo result row becomes one case, or one repeat of a case when you ran with `--repeat`.

| EvalGate | From promptfoo |
|---|---|
| `case_id` | `testCase.description` (or `--case-id`, below) |
| `status: error` | a promptfoo error (`failureReason` 2, such as a provider failure, timeout or a grader that crashed), or a grader error. The error text is replaced with a fixed message, since promptfoo's messages can contain the prompt, the response or local paths. |
| `scores.score` | the overall `score` |
| `scores.<metric>` | each named metric in `namedScores` (assertions with `metric:`) |
| `checks.pass` | whether the test passed (`success`) |
| `checks.<metric>` | whether every assertion with that `metric:` passed. A `metric:` set on an `assert-set` gives a score but no check. |
| `metrics.latency_ms`, `metrics.cost`, `metrics.tokens` | `latencyMs`, `cost`, `tokenUsage.total`. Left out for cached responses, which replay stored values rather than this run's latency and cost. Negative values are rejected. |
| `metadata` | text, number and true/false values of the test's `metadata`. A `category` there works with [category limits](statistics.md#category-limits). |
| `repeat` | `repeatIndex` |

Prompts, outputs and error messages are never copied. Vars are copied only if you choose one as the case id (below).

An assertion that fails, including a `javascript` assertion that throws, is an ordinary test failure in promptfoo (`failureReason` 1), so it shows up as a failing check, not an error.

A typical policy for promptfoo results:

```yaml
version: 1
metrics:
  quality:
    path: scores.quality          # a named metric from your assertions
    direction: maximize
    max_mean_regression_abs: 0.02
  latency:
    path: metrics.latency_ms
    direction: minimize
    max_mean_regression_pct: 10
checks:
  required: [pass]                # every test must pass
  max_new_failures: 0
```

If a policy uses `latency_ms` or `cost`, run promptfoo with `--no-cache`. Otherwise cached cases have no latency or cost, so EvalGate reports the metric as missing (INVALID) rather than mixing in stored values. The same goes for a named metric only some tests assert: a policy on it needs every test to assert it.

## Case identity

EvalGate matches baseline and candidate cases by `case_id`, so it has to be stable and unique. promptfoo has no stable test id, so the default is the test's `description`. Every test needs one, and no two tests may share it. Otherwise the import fails and names the tests.

Use `--case-id vars.<name>` or `--case-id metadata.<name>` to take the id from a variable or a metadata field instead. That value is written to the run as the case id, so prefer a short id field over a var that holds prompt input. promptfoo's test index is deliberately not an option: it changes when tests are added, removed, filtered or reordered, which would silently compare the wrong cases.

## Several providers or prompts

An EvalGate run is one version of one system. If the file holds several providers or prompts, the import fails and lists them. Pick one with `--provider` and/or `--prompt`, using the names the error lists:
- `--provider` takes a label or an id. A label wins, so a label that equals another provider's id still picks the labelled one. Providers that share a label are picked by id.
- `--prompt` takes a prompt id, or its first 8+ characters, or a label you set yourself. Prompt text is never shown, since promptfoo's default label is the whole prompt. To compare two providers from the same file, import each one as its own run.

## Fail closed

Every bad row is reported, and no run files are written unless all rows convert. Errors include:
- a file that isn't a promptfoo results file, another format version, or rows of an unexpected shape (`import_parse`, `import_format`, `import_record`)
- a `failureReason` other than 0, 1 or 2, since a provider error can't then be told apart from a test failure
- missing or duplicate case ids (`import_record`, `duplicate_case_id`)
- a score that isn't a finite number, or `success` that isn't true/false
- a named metric called `score` or `pass`, which would clash with the overall score or check
- rows with and without `repeatIndex` in the same file
