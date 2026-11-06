# Langfuse experiments

Langfuse has no local file export for experiment results, so EvalGate has no `--from langfuse` adapter. If you run experiments with the Langfuse Python SDK, write the EvalGate run from the same script instead. `EvalRun` does the writing, and nothing extra is installed or sent anywhere.

Checked against the Langfuse Python SDK 5.2, with experiments on local data.

## Recipe

Run the experiment as usual, then pass the result and the data you ran on to `write_evalgate_run`:

```python
from evalgate import EvalRun


def write_evalgate_run(result, data, path, *, run_id, expected, case_id, **manifest):
    """Write a Langfuse ExperimentResult as an EvalGate run.

    `expected`: names of the evaluations every item must have.
    `case_id(item)`: a stable id for an item (a dataset item's `id`, or your own field).
    """
    seen = set()
    with EvalRun(run_id, path=path, **manifest) as run:
        for r in result.item_results:
            cid = case_id(r.item)
            seen.add(cid)
            evals = {e.name: e for e in r.evaluations}
            if set(evals) != set(expected):
                # A failing evaluator is only logged, and its evaluations are missing.
                run.record_error(cid, "evaluation missing: " + ", ".join(sorted(set(expected) - set(evals))))
                continue
            run.record(
                cid,
                scores={n: float(e.value) for n, e in evals.items() if e.data_type in (None, "NUMERIC")},
                checks={n: e.value for n, e in evals.items() if e.data_type == "BOOLEAN"},
            )
        for item in data:
            if case_id(item) not in seen:
                # A failing task is only logged, and the item is left out of the result.
                run.record_error(case_id(item), "task failed")
```

```python
from langfuse import Langfuse

langfuse = Langfuse()
data = [
    {"input": "How long do refunds take?", "expected_output": "5 days", "metadata": {"id": "refund-policy"}},
    # ...
]
result = langfuse.run_experiment(name="support-bot", data=data, task=task, evaluators=[correctness])

write_evalgate_run(
    result, data, "evals/candidate",
    run_id="candidate", expected=["correct", "relevance"],
    case_id=lambda item: item["metadata"]["id"],
    dataset="support-suite", dataset_version="v3", evaluator_version="lf-evals-1",
)
```

For a dataset hosted in Langfuse, pass the dataset's items as `data` and use the dataset item id: `data=dataset.items` and `case_id=lambda item: item.id`. Dataset item ids stay the same across runs, so they match baseline and candidate cases correctly. For local data, give every item an id of your own, as above. Never use the position in the list: it changes when items are added or moved.

Then compare as usual:

```bash
evalgate compare evals/baseline evals/candidate --policy evalgate.yaml
```

## What becomes what

| EvalGate | From Langfuse |
|---|---|
| `scores.<name>` | NUMERIC evaluations, and evaluations without a `data_type` (Langfuse treats those as numeric) |
| `checks.<name>` | BOOLEAN evaluations |
| `status: error` | an item whose task failed (Langfuse leaves it out of `item_results`), or whose evaluations are incomplete (a failing evaluator) |

CATEGORICAL evaluations, run-level evaluations, comments and outputs aren't written. Langfuse's item results carry no latency or cost. To gate on those, measure them in your task and pass them as `metrics={...}` to `run.record`.

## Why the failure handling matters

Langfuse keeps going when a task or an evaluator raises, and only logs it. Checked with SDK 5.2:
- A task that raises leaves its item out of `result.item_results` entirely.
- An evaluator that raises leaves that item without the evaluator's evaluations.

Writing only `item_results` would therefore drop failed items silently, and a candidate that crashes on hard cases could look better than the baseline. The recipe records both as errors, so they count against `max_execution_errors` (default 0) instead.
