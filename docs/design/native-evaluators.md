# Design: native evaluators (SPEC Phase 6)

Status: **proposed** (2026-11-19). Nothing here is implemented.

## Problem

EvalGate compares results that something else already scored. Teams without an eval framework still write the same small checks by hand: is the output valid JSON, does it match a schema, does it contain the refusal phrase, is it under N characters. Each team rewrites them, versions them loosely, and lets an exception in a checker crash the run or silently count as a pass.

SPEC Phase 6 asks EvalGate to compute *selected* results itself, under one hard constraint: **native evaluation stays optional.** Offline comparison of precomputed results must work exactly as it does today, and nobody has to use EvalGate's evaluators to use its gate.

## Principle

**Evaluators sit in front of the engine, never inside it.** An evaluator turns one output into a score or a check. Its result is written into an ordinary `cases.jsonl` line, which the existing reader, validator, comparison, policy and reports handle unchanged.

```
outputs ──▶ evaluators ──▶ CaseResult (scores / checks) ──▶ run dir ──▶ engine (unchanged)
          (new, optional)                                    (existing pipeline)
```

So:
- `comparison/`, `policies/`, `validation.py` and the report schema need no evaluator-specific logic. The exceptions are a typed `evaluators` manifest field and two new pair checks (section 4).
- A run scored by native evaluators can't be told apart from one written by hand, except by its manifest.
- Repeats need no new code. Evaluators run on each repeat, and the existing collapse rules apply.

## 1. Evaluator interface

```python
from evalgate.evaluators import Evaluator, check, score

@check(name="cites_policy", version="1")
def cites_policy(output: str, case: dict) -> bool:
    return "refund policy" in output.lower()
```

- An evaluator is a callable `(output, case) -> bool | float`, plus a `name`, a `version` and a `kind` (`check` or `score`). `case` is the dataset record (expected answer, metadata), and is read-only.
- `kind` decides where the result goes: `checks.<name>` or `scores.<name>`. Returning the wrong type (a float from a check, a bool or NaN from a score) is an evaluator error, not a coercion. That matches the strict `Number` / `Check` types in `models/run.py`.
- `name` and `version` are required non-empty strings. For a custom evaluator, bumping `version` when its behaviour changes is the author's job. EvalGate can't detect a code change.
- Two evaluators with the same `name` in one run raise `ValueError` when `EvalRun(...)` is constructed.
- **A failing check vs. an evaluator error.** If the *output* is bad (it isn't JSON, isn't a number, or is too long), the check returns `False`. If the *evaluator* can't run (a missing `case[field]`, an invalid schema, a bug), it raises. Only a raise gives `status: error`. Otherwise bad outputs would count against `max_execution_errors` instead of failing the check.
- Metric direction stays in the policy. An evaluator never says whether higher is better.
- No class hierarchy, registry or plugin entry points. Decorators attach metadata to plain functions.

## 2. Built-in deterministic evaluators (first slice)

All of them are pure, offline, stdlib-only and deterministic. Each is a factory that returns an `Evaluator`. Every factory requires `name=`, so two `contains(...)` checks can't collide on a default name.

**A built-in's version covers its arguments:** `<impl_version>+<first 12 hex of sha256(canonical sorted-key JSON of the arguments)>`. For `json_schema`, the hash is over the schema itself. `matches_model` also requires `version=`, hashed together with `Model.model_json_schema()`: the schema doesn't capture `@field_validator` / `@model_validator` logic or custom types, so a change to those has to bump the declared version, as with a custom evaluator. Every argument must be canonically JSON-serializable, so `exact_match(normalize=...)` takes a tuple from a fixed set (`"strip"`, `"casefold"`) and never a callable. `impl_version` is fixed by EvalGate and changes whenever the behaviour changes. As a result, `length(max=2000)` and `length(max=500)` are different evaluators, and comparing them is INVALID.

| Factory (all take `name=`) | Kind | Passes when | Raises (evaluator error) when |
|---|---|---|---|
| `json_valid()` | check | output parses as JSON (strict: no NaN/Infinity, no duplicate keys) | never |
| `matches_model(PydanticModel, *, version)` | check | output validates against a Pydantic model. Pydantic is already a dependency | never (an invalid model fails at construction) |
| `json_schema(schema)` | check | output validates against a JSON Schema. Needs the optional `evalgate-ai[schema]` extra (`jsonschema`) | never (an invalid schema or missing extra fails at construction) |
| `regex(pattern, *, must_match=True)` | check | pattern found (or not found) | never (a bad pattern fails at construction) |
| `contains(text, *, ignore_case=False)` / `not_contains(...)` | check | substring present / absent | never |
| `exact_match(field="expected", *, normalize=())` | check | output equals `case[field]` after the chosen `"strip"` / `"casefold"` steps | `case[field]` is missing |
| `length(*, min=None, max=None, unit="chars")` | check | length within bounds | never |
| `numeric_close(field="expected", *, abs_tol, rel_tol)` | check | output parses as a number within tolerance (unparseable output: `False`) | `case[field]` is missing or not a number |

Deliberately left out: fuzzy similarity (BLEU, ROUGE, embedding distance). These are hard to version reproducibly and are better imported from tools that own them.

## 3. Running evaluators

**SDK (first slice).** `EvalRun.record` gains `output=` and `evaluators=`:

```python
EVALUATORS = [
    json_valid(name="is_json"),
    matches_model(Answer, name="answer_shape", version="1"),
    length(max=2000, name="not_too_long"),
    cites_policy,
]

with EvalRun("cand-v2", dataset="support", dataset_version="v3",
             evaluator_version="native", evaluators=EVALUATORS) as run:
    for case in dataset:
        run.record(case["id"], output=agent(case), case=case, metrics={"latency_ms": ms})
```

- `output` and `case` go to the evaluators and are **never written**. `case` can hold prompts and expected answers. Only the explicit `metadata=` reaches `cases.jsonl`. That's the existing privacy rule: raw prompts and outputs are kept out unless explicitly asked for.
- Evaluator results are merged into `scores` / `checks`. A name clash with a value the caller passed directly is a `ValueError` at record time.
- **Fail closed:** if any evaluator raises an `Exception` or returns the wrong type, the case is written as `status: error` with the fixed message `evaluator <name> failed`. The exception text is not copied (it can quote the output). That's the same approach as the DeepEval adapter's errored metrics. A broken checker therefore never counts as a pass, and the existing rules treat the case as an error. `BaseException` (Ctrl-C, `SystemExit`) is not caught: it aborts the `with` block, which already discards the partial run.

**CLI (second slice).** `evalgate evaluate OUTPUTS.jsonl --config evaluators.yaml --out DIR` is for teams that don't use Python.
- Each line of `OUTPUTS.jsonl` holds `case_id`, `status` (`ok` or `error`), plus `output` when ok, `error` when errored, and optionally `expected`, `metadata`, `metrics` and `repeat`. So the application under test can record its own per-case failures.
- `evaluators.yaml` lists built-ins with their `name` and arguments.
- Custom Python evaluators come only through the SDK, since loading code from a YAML path is an arbitrary-code-execution surface that isn't worth it here.
- `write_rows` is extended to pass `output` and `evaluators` through the same `EvalRun` path, so the CLI and SDK share one engine, and the all-or-nothing write and `--overwrite` behave as they do for imports.

## 4. Versioning (fail closed)

Today a pair is INVALID when `evaluator_version` differs. Native evaluators make that check sharper:

- `evaluators` becomes a typed `RunManifest` field (`dict[NonEmptyStr, NonEmptyStr] | None`) and a reserved name. `EvalRun(evaluators=...)` writes `"evaluators": {"<name>": "<version>", ...}` (sorted) into `run.json`, with `schema_version: "1.2"` only when it is set. That's the same pattern as `repeats` and 1.1.
  - 1.2 is a superset of 1.1. A run's `schema_version` is the lowest version that covers every feature it uses: the writer picks 1.2 if `evaluators` is set, otherwise 1.1 if `repeats` is set, otherwise 1.0.
  - The `schema_version` `Literal` gains `"1.2"`, and the repeats check in `validate_run` (today `!= "1.1"`) accepts `"1.1"` or `"1.2"`.
  - Runs without native evaluators serialize exactly as before.
  - EvalGate 0.6 rejects a 1.2 file with a schema error rather than ignoring the map and possibly passing.
  - A pre-1.2 `run.json` that used `evaluators` as free metadata is rejected or INVALID, never misread, as `repeat` / `repeats` were in 0.3.0.
- Two new pair checks, both via `_diag(rules.require_same_evaluator, ...)` like `evaluator_mismatch`. That means an error by default and a warning when the policy opts out:
  - `evaluator_set_mismatch`: both runs declare `evaluators` and the maps differ.
  - `evaluator_set_partial`: only one run declares them. For example, a hand-scored baseline against a natively scored candidate with the same `evaluator_version` and check names isn't the same evaluator.
- `evaluator_version` stays required and user-chosen. EvalGate does not derive it. That avoids a silent change in what counts as "the same evaluator".

## 5. LLM judges (third slice, optional extra)

Only after the deterministic slices ship and get used. It meets the SPEC constraint like this:

- It ships as `evalgate-ai[judge]`, with **no provider SDK dependency**. The user passes `complete: Callable[[str], str]` (their own client). EvalGate never makes network calls. The user's callable does.
- `judge(rubric, complete, *, model_id, kind="score"|"check")`. A rubric is a versioned prompt template with a strict output format, so a reply that doesn't parse is an evaluator error, which makes the case `status: error`, never a default score.
- Its version is `rubric.version + model_id`, so changing the judge model or rubric makes a comparison INVALID through `evaluator_set_mismatch`. `model_id` is a label the user declares: EvalGate can't verify that `complete` actually calls that model. Keeping them in sync, and testing for drift, is on the user.
- **Cache:** a local directory keyed by `sha256(model_id, rubric version, rendered prompt)`, storing only the parsed result, never the prompt or the reply. It makes reruns cheap and reproducible. It's opt-in (`cache_dir=`), because a cached judge hides model drift by design.
- **Calibration:** `evalgate calibrate --judge-run DIR --labels human.jsonl` reports agreement with human labels per check or score (accuracy and Cohen's κ for checks, MAE and Spearman for scores). It is reporting only. It never changes decisions.

## 6. Non-goals

- **Pairwise preference judging** (baseline vs candidate output for each case). Its result is a comparison, not a per-run value, so it doesn't fit the run → run model and would need a new report section and policy rules. Deferred until someone asks for it.
- Running the application under test, generating datasets, or hosting judges.
- Any change to comparison, statistics, policy semantics or exit codes.
- Copying outputs, prompts or judge explanations into run files or reports.

## 7. Release plan

| Release | Scope | Acceptance |
|---|---|---|
| **0.7.0** | `evaluators/` package: interface, built-ins from section 2, `EvalRun(evaluators=)` + `record(output=)`, typed `evaluators` manifest field (schema 1.2), `evaluator_set_mismatch` / `_partial`, docs page, `examples/native-evaluators` | An evaluator that raises gives `status: error`, never a pass. A bad output fails the check instead. No output or `case` text appears in `cases.jsonl`. Runs without native evaluators serialize and compare exactly as in 0.6.0. A changed evaluator version **or argument** is INVALID, and so is a run with native evaluators compared to one without. Duplicate evaluator names are rejected. A run with both repeats and native evaluators round-trips and compares. Core has no new dependency. |
| **0.8.0** | `evalgate evaluate` CLI + `evaluators.yaml` | The CLI and SDK give byte-identical `run.json` and `cases.jsonl` for the same outputs and config. Bad config or a bad outputs line exits 2 and writes nothing. |
| **0.9.0** | `[judge]` extra: rubric, `judge()`, cache, `calibrate` | No network without a user callable. Unparseable judge reply gives `status: error`. A cache hit equals a fresh call when the judge is deterministic. Calibration figures are checked against hand-computed examples. |

Each slice gets its own tests, CHANGELOG entry and docs page, following the 0.3.0–0.6.0 pattern.

## 8. Open questions

1. Should `record(output=...)` also accept `store_output=True`, to keep outputs in the run for debugging? The SPEC allows it "when explicitly requested". The proposal is no for 0.7.0: add it only on request.
2. Should `json_schema` be an optional extra or vendored? The proposal is an extra: vendoring a JSON Schema validator is a lot of code to own.
3. Should an evaluator be able to return `None` ("not applicable to this case")? The proposal is no. Missing values already make a comparison INVALID (`metric_missing`), and allowing `None` would just reintroduce that through a side door. Put the case in a category instead.
