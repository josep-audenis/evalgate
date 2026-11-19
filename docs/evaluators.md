# Native evaluators

EvalGate normally compares results that another tool already scored. If you don't have an eval framework, or only need a few simple checks, EvalGate can score outputs itself while you record a run. Doing so is optional: everything else works the same with precomputed results.

Evaluators run before the comparison, inside the Python SDK or with [`evalgate evaluate`](#without-python-evalgate-evaluate). Each one turns an output into a check (`true`/`false`) or a score, written into `cases.jsonl` like any other value. Comparison, policies, statistics and reports don't change.

```python
from pydantic import BaseModel
from evalgate import EvalRun
from evalgate.evaluators import json_valid, length, matches_model, not_contains

class Answer(BaseModel):
    answer: str
    confidence: float

EVALUATORS = [
    json_valid(name="is_json"),
    matches_model(Answer, name="answer_shape", version="1"),
    length(name="concise", max=300),
    not_contains("as an AI", name="no_ai_disclaimer", ignore_case=True),
]

with EvalRun("candidate", dataset="support-suite", dataset_version="v3",
             evaluator_version="native-v1", evaluators=EVALUATORS) as run:
    for case in dataset:
        run.record(case["id"], output=agent(case), case=case, scores={"quality": grade(case)})
```

Then gate on the checks in `evalgate.yaml` as usual:

```yaml
checks:
  required: [is_json, answer_shape, concise, no_ai_disclaimer]
  max_new_failures: 0
```

A runnable version is in [examples/native-evaluators](../examples/native-evaluators): the candidate's average quality improves, but one answer comes back wrapped in a Markdown code fence, so `is_json` fails and the release is blocked.

## Without Python: `evalgate evaluate`

Write one JSON line per case to an outputs file, list the evaluators in a YAML file, and convert:

```bash
evalgate evaluate outputs.jsonl --config evaluators.yaml --out evals/candidate
evalgate compare evals/baseline evals/candidate --policy evalgate.yaml
```

`outputs.jsonl`, one object per line:

```json
{"case_id": "refund-001", "status": "ok", "output": "{\"answer\": \"...\"}", "expected": "...", "scores": {"quality": 0.86}, "metadata": {"category": "billing"}}
{"case_id": "refund-002", "status": "error", "error": "timeout"}
```

| Field | |
|---|---|
| `case_id` | Required, non-empty. |
| `status` | `ok` or `error` (the application failed on this case). |
| `output` | Required when `ok`, not allowed when `error`. Any JSON value; `null` counts as an output. |
| `error` | Only with `status: error` (even `null` is rejected on an `ok` line). Optional; defaults to `"error"`. An `error` line takes only `case_id`, `status`, `error`, `metadata` and `repeat`. |
| `expected` | Optional. What `exact_match` and `numeric_close` compare against (`field: expected`). Absent is different from `null`: an evaluator that needs it and doesn't find it is an evaluator error. |
| `scores`, `metrics` | Optional numbers you already have, such as a grader's score or latency, recorded as they are. A score or metric named like one of your evaluators is an error (`outputs_record`), so it can't be mistaken for that evaluator's result. |
| `metadata` | Optional object, such as `{"category": "billing"}` for [category limits](statistics.md#category-limits). |
| `repeat` | Optional [repeat](design/repeated-runs.md) index, on every line or on none (`outputs_record` otherwise). |

Evaluators see the case as `{"case_id", "metadata", "expected"}`: never `scores`, `metrics` or the error.

`evaluators.yaml`:

```yaml
version: 1
manifest:                      # the same block as an import mapping
  dataset_id: support-suite
  dataset_version: v3
  evaluator_version: native-v1
  # run_id, application, application_version: optional; --run-id overrides run_id
store_output: false            # optional; true stores each output in cases.jsonl
evaluators:
  - type: json_valid
    name: is_json
  - type: json_schema
    name: answer_shape
    schema_file: answer.schema.json   # inside this config's directory; or inline `schema:`
  - type: length
    name: concise
    max: 300
  - type: not_contains
    name: no_ai_disclaimer
    text: as an AI
    ignore_case: true
```

Each entry has a `type` (any built-in below except `matches_model`, which needs a Python class) and a `name`, plus that evaluator's arguments with the same names as in Python. Custom evaluators are Python-only: the config never loads code. The versions are the same as the Python API's, so a run written by `evalgate evaluate` and one written by `EvalRun` with the same evaluators compare as equal, and the files are byte-identical.

Fails closed, like `evalgate import`: unknown keys and wrong types are rejected, every bad line or config entry is reported, and nothing is written unless everything is valid (exit 2). Messages give a line number and a field name, never an output's value. Evaluator errors are not input errors: those cases are written as `status: error`, and the command says how many there were.

| Code | Meaning |
|---|---|
| `evaluators_yaml` | The config isn't valid YAML (or has a duplicate key). |
| `schema` | The config doesn't match the format above. |
| `evaluators_config` | An evaluator can't be built (bad arguments, invalid or remote JSON Schema, `schema_file` problem, missing `[schema]` extra), or two share a name. |
| `outputs_parse` | A line isn't a JSON object, has a duplicate key, or a non-finite number (`NaN`, `1e999`). |
| `outputs_record` | A line doesn't match the table above, repeats a `case_id` (and `repeat`), names a score or metric like an evaluator, or `repeat` is on some lines only. |
| `no_cases` | The outputs file has no lines. |
| `import_run_id` | No run id in the manifest or `--run-id`. |
| `import_exists` | `--out` already holds a run (pass `--overwrite`), or it or a parent is a file. |

Missing or unreadable files give the usual `file_missing` / `file_unreadable`.

The SDK equivalent is `evalgate.evaluate_outputs(source, config, out, *, run_id=None, overwrite=False)`.

## LLM judges

A judge asks your own model client to grade each output against a versioned rubric, and records a check or a score like any other evaluator. Python only, no extra dependency, no network calls from EvalGate. See [judges.md](judges.md), which also covers `evalgate calibrate`.

## Built-in evaluators

All of them are checks, and each needs a `name=`, which becomes the check's name in `cases.jsonl` and in your policy.

| Evaluator | Passes when |
|---|---|
| `json_valid(name=)` | The output is a string of strict JSON: no `NaN`/`Infinity`, no duplicate keys. |
| `matches_model(Model, name=, version=)` | The output (JSON text, or a Python value) validates against a Pydantic model. JSON text is parsed as strictly as `json_valid`. |
| `json_schema(schema, name=)` | The output is strict JSON valid under a JSON Schema (Draft 2020-12). Needs `pip install 'evalgate-ai[schema]'`. |
| `regex(pattern, name=, must_match=True)` | `pattern` is found in the output (with `must_match=False`: not found). |
| `contains(text, name=, ignore_case=False)` | The output contains `text`. |
| `not_contains(text, name=, ignore_case=False)` | The output doesn't contain `text`. |
| `exact_match(name=, field="expected", normalize=())` | The output equals `case[field]`. For strings, `normalize` can apply `"strip"` and/or `"casefold"` first. Other values must have the same type at every level (`True` doesn't match `1`). |
| `length(name=, min=None, max=None, unit="chars")` | The output string's length is within the bounds. `unit="words"` counts whitespace-separated words. |
| `numeric_close(name=, field="expected", abs_tol=0, rel_tol=0)` | The output (a number, or a string Python's `float()` parses) is within tolerance of `case[field]`, as `math.isclose` defines it. `case[field]` must be a number: a missing field or a string such as `"3"` is an evaluator error. |

Notes:
- `json_schema` works offline: only local `$ref`s (`"#..."`) are allowed, and a schema with a remote reference is rejected when the evaluator is created. `format` is an annotation only, as Draft 2020-12 specifies by default.
- `regex` uses Python's `re`, which has no timeout. Avoid patterns with catastrophic backtracking, such as `(a+)+$`.
- Bad arguments (an invalid pattern, schema or model, empty text, reversed bounds, a negative tolerance) raise when the evaluator is created, not on the first case.

## Your own evaluators

Decorate a function that takes the output and the dataset record:

```python
from evalgate.evaluators import check, score

@check(name="cites_policy", version="1")
def cites_policy(output, case):
    return "refund policy" in output.lower()

@score(name="brevity", version="2")
def brevity(output, case):
    return 1 / (1 + len(output.split()))
```

- A check must return a `bool`, and a score a finite number. Any other value (including `None`, `NaN`, or `1` from a check) is an evaluator error, never coerced.
- **Bump `version` whenever the function's behaviour changes.** EvalGate can't see your code. The version is how it knows that two runs were scored the same way.
- `case` is a read-only copy, and `output` is a copy too, so an evaluator can't change what another one sees.
- The decorated object is still callable, so you can unit-test it directly: `cites_policy("see our refund policy", {})`.

## Failing a check vs. an evaluator error

- **A bad output fails the check.** Output that isn't JSON, is too long, or isn't a number gives `false`. This is what a check is for.
- **An evaluator that can't run is an error.** If an evaluator raises (for example `exact_match` when the case has no `expected` field) or returns the wrong type, the case is recorded as `status: error` with the message `evaluator <name> failed`. The exception text isn't copied, because it can quote the output. Errors count against `max_execution_errors` and never as a pass.
- `KeyboardInterrupt` and other `BaseException`s are not caught: they abort the `with` block, and the unfinished run is discarded.

## Versions and comparability

`EvalRun(evaluators=...)` declares each evaluator's name and version in `run.json` and uses `schema_version: "1.2"`:

```json
"evaluators": {"answer_shape": "1+0f3bd0dd27e0", "is_json": "1+f0d7efc93621"}
```

A built-in's version includes a hash of its arguments, so `length(max=300)` and `length(max=500)` have different versions. `matches_model` also hashes the model's JSON schema, but a schema can't show validator code or settings such as strict mode, so you pass `version=` and bump it yourself. `json_schema` includes the installed `jsonschema` major version.

When you compare two runs:

| Situation | Diagnostic | Default |
|---|---|---|
| Both runs declare evaluators, but a name or version differs | `evaluator_set_mismatch` | INVALID |
| Only one run declares evaluators (for example a hand-scored baseline) | `evaluator_set_partial` | INVALID |
| A run declares `evaluators` with a schema before 1.2 | `evaluators_schema` | INVALID |

The first two follow `comparison.require_same_evaluator`, like `evaluator_mismatch`: with `false` they become warnings. You still choose `evaluator_version` yourself, so you can also change it to mark other changes to how you score.

Scoring the baseline again with the same evaluators is usually the right fix for a mismatch.

## Storing outputs

By default `output` and `case` are passed to the evaluators and never written. To keep outputs for debugging, use `EvalRun(..., store_output=True)`:

- Every `ok` case then needs `output=`, and the output is written to `cases.jsonl` as an `output` field. It's taken before the evaluators run.
- It must be JSON: a string, number, boolean, `null`, list or object. `NaN` and infinity are rejected. A `None` output is valid, but isn't written, like any other unset field.
- Cases that error never store output.
- `store_output` works without evaluators too, and on its own doesn't change the schema version.
- Stored outputs are never shown in any report (terminal, JSON, HTML, Markdown), and never affect decisions.

`output` was free metadata before 0.7.0, so older runs that use it still load and decide unchanged.

## Repeated runs

Evaluators run on each repeat: `run.record(case_id, repeat=i, output=...)` with `EvalRun(repeats=k, evaluators=...)`. Repeats are then combined as usual: a check passes only if it passes in every repeat, and an evaluator error in any repeat makes the case an error.
