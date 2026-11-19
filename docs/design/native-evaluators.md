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
- `comparison/`, `policies/`, `validation.py` and the report schema need no evaluator-specific logic. The one exception is a new pair check (section 4).
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
- Metric direction stays in the policy. An evaluator never says whether higher is better.
- No class hierarchy, registry or plugin entry points. Decorators attach metadata to plain functions.

## 2. Built-in deterministic evaluators (first slice)

All of them are pure, offline, stdlib-only and deterministic. Each is a factory that returns an `Evaluator`. Its `version` is fixed by EvalGate and changes whenever its behaviour changes.

| Factory | Kind | Passes when |
|---|---|---|
| `json_valid()` | check | output parses as JSON (strict: no NaN/Infinity, no duplicate keys) |
| `matches_model(PydanticModel)` | check | output validates against a Pydantic model. Pydantic is already a dependency |
| `json_schema(schema)` | check | output validates against a JSON Schema. Needs the optional `evalgate-ai[schema]` extra (`jsonschema`); a clear error if it's missing |
| `regex(pattern, *, must_match=True)` | check | pattern found (or not found) |
| `contains(text, *, ignore_case=False)` / `not_contains(...)` | check | substring present / absent |
| `exact_match(field="expected", *, normalize=...)` | check | output equals `case[field]` after optional strip / casefold |
| `length(*, min=None, max=None, unit="chars")` | check | length within bounds |
| `numeric_close(field="expected", *, abs_tol, rel_tol)` | check | output parses as a number within tolerance |

Deliberately left out: fuzzy similarity (BLEU, ROUGE, embedding distance). These are hard to version reproducibly and are better imported from tools that own them.

## 3. Running evaluators

**SDK (first slice).** `EvalRun.record` gains `output=` and `evaluators=`:

```python
EVALUATORS = [json_valid(), matches_model(Answer), length(max=2000), cites_policy]

with EvalRun("cand-v2", dataset="support", dataset_version="v3",
             evaluator_version="native", evaluators=EVALUATORS) as run:
    for case in dataset:
        run.record(case["id"], output=agent(case), case=case, metrics={"latency_ms": ms})
```

- `output` goes to the evaluators and is **never written** to `cases.jsonl`. That's the existing privacy rule: raw outputs are kept out unless explicitly asked for.
- Evaluator results are merged into `scores` / `checks`. A name clash with a value the caller passed directly is a `ValueError` at record time.
- **Fail closed:** if any evaluator raises or returns the wrong type, the case is written as `status: error` with the fixed message `evaluator <name> failed`. The exception text is not copied (it can quote the output). That's the same approach as the DeepEval adapter's errored metrics. A broken checker therefore never counts as a pass, and the existing rules treat the case as an error.

**CLI (second slice).** `evalgate evaluate OUTPUTS.jsonl --config evaluators.yaml --out DIR` is for teams that don't use Python. Each line of `OUTPUTS.jsonl` holds `case_id`, `output`, optional `expected`, `metadata`, `metrics` and `repeat`. `evaluators.yaml` lists built-ins with their arguments. Custom Python evaluators come only through the SDK, since loading code from a YAML path is an arbitrary-code-execution surface that isn't worth it here. It reuses `write_rows` from the importer, so the all-or-nothing write and `--overwrite` behave the same way.

## 4. Versioning (fail closed)

Today a pair is INVALID when `evaluator_version` differs. Native evaluators make that check sharper:

- `EvalRun(evaluators=...)` writes `"evaluators": {"<name>": "<version>", ...}` (sorted) into `run.json`. Manifest extras are already allowed, so `schema_version` stays `1.1`.
- New pair check `evaluator_set_mismatch` (error): both runs declare `evaluators` and the maps differ. If only one run declares them, you get a warning (`evaluator_set_partial`).
- `evaluator_version` stays required and user-chosen. EvalGate does not derive it. That avoids a silent change in what counts as "the same evaluator".

## 5. LLM judges (third slice, optional extra)

Only after the deterministic slices ship and get used. It meets the SPEC constraint like this:

- It ships as `evalgate-ai[judge]`, with **no provider SDK dependency**. The user passes `complete: Callable[[str], str]` (their own client). EvalGate never makes network calls. The user's callable does.
- `judge(rubric, complete, *, model_id, kind="score"|"check")`. A rubric is a versioned prompt template with a strict output format, so a reply that doesn't parse is an evaluator error, which makes the case `status: error`, never a default score.
- Its version is `rubric.version + model_id`, so changing the judge model or rubric makes a comparison INVALID through `evaluator_set_mismatch`.
- **Cache:** a local directory keyed by `sha256(model_id, rubric version, rendered prompt)`, storing only the parsed result, never the prompt or the reply. It makes reruns cheap and deterministic. It's opt-in (`cache_dir=`), because a cached judge hides model drift by design.
- **Calibration:** `evalgate calibrate --judge-run DIR --labels human.jsonl` reports agreement with human labels per check or score (accuracy and Cohen's κ for checks, MAE and Spearman for scores). It is reporting only. It never changes decisions.

## 6. Non-goals

- **Pairwise preference judging** (baseline vs candidate output for each case). Its result is a comparison, not a per-run value, so it doesn't fit the run → run model and would need a new report section and policy rules. Deferred until someone asks for it.
- Running the application under test, generating datasets, or hosting judges.
- Any change to comparison, statistics, policy semantics or exit codes.
- Copying outputs, prompts or judge explanations into run files or reports.

## 7. Release plan

| Release | Scope | Acceptance |
|---|---|---|
| **0.7.0** | `evaluators/` package: interface, built-ins from section 2, `EvalRun(evaluators=)` + `record(output=)`, `evaluators` manifest map, `evaluator_set_mismatch`, docs page, `examples/native-evaluators` | An evaluator that raises gives `status: error`, never a pass. No output text appears in `cases.jsonl`. Runs with and without native evaluators compare identically. A changed evaluator version is INVALID. Core has no new dependency. |
| **0.8.0** | `evalgate evaluate` CLI + `evaluators.yaml` | The CLI and SDK give byte-identical `cases.jsonl` for the same outputs and config. Bad config or a bad outputs line exits 2 and writes nothing. |
| **0.9.0** | `[judge]` extra: rubric, `judge()`, cache, `calibrate` | No network without a user callable. Unparseable judge reply gives `status: error`. A cache hit equals a fresh call when the judge is deterministic. Calibration figures are checked against hand-computed examples. |

Each slice gets its own tests, CHANGELOG entry and docs page, following the 0.3.0–0.6.0 pattern.

## 8. Open questions

1. Should `record(output=...)` also accept `store_output=True`, to keep outputs in the run for debugging? The SPEC allows it "when explicitly requested". The proposal is no for 0.7.0: add it only on request.
2. Should `json_schema` be an optional extra or vendored? The proposal is an extra: vendoring a JSON Schema validator is a lot of code to own.
3. Should an evaluator be able to return `None` ("not applicable to this case")? The proposal is no. Missing values already make a comparison INVALID (`metric_missing`), and allowing `None` would just reintroduce that through a side door. Put the case in a category instead.
