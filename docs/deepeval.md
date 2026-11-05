# Importing DeepEval results

`evalgate import --from deepeval` turns a DeepEval test run file into an EvalGate run. No mapping file is needed. Save one test run for the baseline version and one for the candidate, import both, then compare.

DeepEval writes a test run file when you give it a results folder:

```python
from deepeval import evaluate
from deepeval.evaluate.configs import DisplayConfig

evaluate(test_cases, metrics, display_config=DisplayConfig(results_folder="deepeval-results"))
```

For `deepeval test run`, set `DEEPEVAL_RESULTS_FOLDER=deepeval-results` instead. Each run writes `deepeval-results/test_run_<timestamp>.json`. Import that file. `.deepeval/.latest_run_full.json` also works. `.deepeval/.latest_test_run.json` works only when you're not logged in to Confident AI; after an upload it holds just a link. With `DEEPEVAL_LOCAL_STORE=sqlite`, DeepEval writes no JSON file at all, so there is nothing to import.

```bash
evalgate import deepeval-results/test_run_20261105_101500.json --from deepeval \
  --out evals/candidate \
  --dataset support-suite --dataset-version v3 --evaluator-version de-metrics-1
evalgate compare evals/baseline evals/candidate --policy evalgate.yaml
```

```python
from evalgate import import_deepeval

import_deepeval(
    "deepeval-results/test_run_20261105_101500.json", "evals/candidate",
    dataset="support-suite", dataset_version="v3", evaluator_version="de-metrics-1",
)
```

The file doesn't say which dataset version or evaluator produced it, so you pass those. Change `--evaluator-version` whenever you change metrics, thresholds or the judge model, so EvalGate refuses to compare results scored differently. `--run-id` defaults to the run's `identifier` (`deepeval test run -id ...`), or else the file name.

Checked against deepeval 4.3.

## What becomes what

Each test case becomes one case. Single-turn (`testCases`) and conversational (`conversationalTestCases`) test cases are both imported.

| EvalGate | From DeepEval |
|---|---|
| `case_id` | the test case `name` (or `--case-id metadata.<key>`, below) |
| `status: error` | any metric of the test case errored: its `error` is set (with `ignore_errors`), or it has a threshold but no score (how `deepeval test run` records a metric that raised). The error text is replaced with a fixed message, since it can quote inputs, outputs or local paths. |
| `scores.<metric>` | each metric's `score`, under the metric's name |
| `checks.<metric>` | whether the metric passed, for metrics with a threshold. Flaky metrics get a score but no check, since they don't decide DeepEval's pass/fail either. |
| `checks.pass` | whether the test case passed (DeepEval's `success`) |
| `metrics.latency_ms` | `completionTime` × 1000, if you set `completion_time` on the test case |
| `metrics.cost`, `metrics.tokens` | `tokenCost`; `inputTokenCount` + `outputTokenCount`. Only when present. Negative values are rejected. |
| `metadata` | text, number and true/false values of the test case `metadata`. A `category` there works with [category limits](statistics.md#category-limits). |

Inputs, outputs, expected outputs, contexts, turns, metric reasons and error messages are never copied. DeepEval's own run time and evaluation cost (`runDuration`, `evaluationCost`) measure the evaluation, not your application, so they aren't imported.

A metric's name becomes a key, so a policy refers to it as `scores.<name>` and `checks.<name>`, with the name exactly as DeepEval reports it:

```yaml
version: 1
metrics:
  relevancy:
    path: scores.Answer Relevancy
    direction: maximize
    max_mean_regression_abs: 0.02
checks:
  required: [pass]
  max_new_failures: 0
```

Metric names that contain a dot are rejected, since a policy path couldn't name them.

Metrics skipped by `skip_on_missing_params` are left out of DeepEval's file altogether. If a policy uses such a metric, the comparison is INVALID (`metric_missing` or `check_missing`) rather than decided on the cases that happen to have it.

## Case identity

EvalGate matches baseline and candidate cases by `case_id`, so it has to be stable and unique. The default is the test case `name`. Give every test case a name of its own: `LLMTestCase(name="refund policy", ...)`.

If you don't, DeepEval names test cases by position: `test_case_0`, `conversational_test_case_0`, and under `deepeval test run` with `@pytest.mark.parametrize("test_case", ...)`, pytest ids like `test_bot[test_case0]` (or `test_bot[test_case0-gpt]` with a second parametrize). Those names change when cases are added, removed or reordered, which would silently compare the wrong cases, so the import rejects them. pytest ids built from another parameter name (`test_bot[case0]`) can't be told apart from names you chose, so they aren't rejected: always pass `ids=` to `parametrize`. Set `name=` on each test case, pass `ids=`, or use `--case-id metadata.<key>` to take the id from a metadata field, for example a ticket or dataset row id.

## Fail closed

Every bad test case is reported, and no run files are written unless all convert. Errors include:
- a file that isn't a DeepEval test run, or test cases of an unexpected shape (`import_parse`, `import_record`)
- missing, position-based or duplicate case ids (`import_record`, `duplicate_case_id`)
- a score that isn't a finite number, a `success` that isn't true/false, a metric reported twice, or a metric called `pass`
