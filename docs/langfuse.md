# Langfuse experiments

Langfuse has no local file export for experiment results, so EvalGate has no `--from langfuse` adapter. If you run experiments with the Langfuse Python SDK, write the EvalGate run from the same script instead. `EvalRun` does the writing, and nothing extra is installed or sent anywhere.

Checked against the Langfuse Python SDK 5.2, with experiments on local data.

## Recipe

Run the experiment as usual, then pass the result and the data you ran on to `write_evalgate_run`:

```python
from evalgate import EvalRun


def write_evalgate_run(result, data, path, *, run_id, expected, case_id, **manifest):
    """Write a Langfuse ExperimentResult as an EvalGate run.

    `expected`: names of the evaluations every item must have, each exactly once.
    `case_id(item)`: a stable id for an item (a dataset item's `id`, or your own field).
    """
    ids = [str(case_id(item)) for item in data]
    if len(ids) != len(set(ids)):  # a duplicate could hide another item's failure
        raise ValueError("case ids in data must be unique")
    seen = set()
    with EvalRun(run_id, path=path, **manifest) as run:
        for r in result.item_results:
            cid = str(case_id(r.item))
            seen.add(cid)
            names = sorted(e.name for e in r.evaluations)
            if names != sorted(expected):
                # A failing evaluator is only logged, and its evaluations are missing.
                run.record_error(cid, f"evaluations {names}, expected {sorted(expected)}")
                continue
            scores, checks = {}, {}
            for e in r.evaluations:
                if e.data_type in (None, "NUMERIC"):
                    scores[e.name] = float(e.value)
                elif e.data_type == "BOOLEAN":  # Langfuse also accepts 1/0 here
                    checks[e.name] = bool(e.value) if e.value in (0, 1) else e.value
                elif e.data_type not in ("CATEGORICAL", "TEXT"):
                    raise ValueError(f"{e.name}: unknown data_type {e.data_type!r}")
            run.record(cid, scores=scores, checks=checks)
        for cid in ids:
            if cid not in seen:
                # A failing task (or an item Langfuse skips) is only logged and left out.
                run.record_error(cid, "task failed or item skipped")
```

```python
from langfuse import Langfuse

langfuse = Langfuse()
data = [
    {"input": "How long do refunds take?", "expected_output": "5 days", "metadata": {"id": "refund-policy"}},
    # ...
]
# task and correctness are your own functions; correctness returns "correct" and "relevance".
result = langfuse.run_experiment(name="support-bot", data=data, task=task, evaluators=[correctness])

write_evalgate_run(
    result, data, "evals/candidate",
    run_id="candidate", expected=["correct", "relevance"],
    case_id=lambda item: item["metadata"]["id"],
    dataset="support-suite", dataset_version="v3", evaluator_version="lf-evals-1",
)
```

For a dataset hosted in Langfuse, run `result = dataset.run_experiment(...)` (which keeps the link to the dataset version), then pass `dataset.items` as `data` and use the dataset item id: `case_id=lambda item: item.id`. Dataset item ids stay the same across runs, so they match baseline and candidate cases correctly. For local data, give every item an id of your own, as above. Never use the position in the list: it changes when items are added or moved.

The helper stops with an error, writing nothing, when ids in `data` aren't unique, or an evaluation has a value EvalGate can't store (a check that isn't true/false or 1/0, a score that isn't a finite number) or an unknown `data_type`.

Then compare as usual:

```bash
evalgate compare evals/baseline evals/candidate --policy evalgate.yaml
```

## What becomes what

| EvalGate | From Langfuse |
|---|---|
| `scores.<name>` | NUMERIC evaluations, and evaluations without a `data_type` (Langfuse treats those as numeric) |
| `checks.<name>` | BOOLEAN evaluations (`True`/`False`, or 1/0) |
| `status: error` | an item whose task failed or that Langfuse skipped (both left out of `item_results`), or whose evaluations don't match `expected` exactly (a failing evaluator, or one returning a name twice or an extra name) |

CATEGORICAL and TEXT evaluations, run-level evaluations, comments and outputs aren't written. Langfuse's item results carry no latency or cost. To gate on those, measure them in your task and pass them as `metrics={...}` to `run.record`.

## Why the failure handling matters

Langfuse keeps going when a task or an evaluator raises, and only logs it. Checked with SDK 5.2:
- A task that raises leaves its item out of `result.item_results` entirely.
- An evaluator that raises leaves that item without the evaluator's evaluations.

Writing only `item_results` would therefore drop failed items silently, and a candidate that crashes on hard cases could look better than the baseline. The recipe records both as errors, so they count against `max_execution_errors` (default 0) instead.
