# Design: native evaluators (SPEC Phase 6)

Status: **0.7.0 implemented** (SDK and built-ins; see [docs/evaluators.md](../evaluators.md)). The 0.8.0 CLI and 0.9.0 judge slices are not implemented yet. Decisions on the former open questions are in section 8.

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
- A run scored by native evaluators can't be told apart from one written by hand, except by its manifest (and, with `store_output=True`, the stored outputs).
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

All of them are pure, offline and deterministic, using only the stdlib or Pydantic, except `json_schema`, which uses the optional extra. Each is a factory that returns an `Evaluator`. Every factory requires `name=`, so two `contains(...)` checks can't collide on a default name.

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

- `output` and `case` go to the evaluators and are **not written** by default. `case` can hold prompts and expected answers, and is never written. Only the explicit `metadata=` reaches `cases.jsonl`. That's the existing privacy rule: raw prompts and outputs are kept out unless explicitly asked for.
- **Opt-in output storage:** `EvalRun(..., store_output=True)` writes each `ok` case's `output` into `cases.jsonl` as an `output` field, for debugging.
  - It's a run-level switch, not a per-call one, so a run either has outputs everywhere or nowhere. With `store_output=True`, an `ok` record without `output=` raises `ValueError` at record time. The reader doesn't enforce this: a hand-edited file with only some outputs is ignored, not an error.
  - `record(output=)` and `store_output` work with or without `evaluators=`. Without evaluators, `output` is only stored (if `store_output=True`) and nothing is evaluated.
  - The field is typed `pydantic.JsonValue | None`. `record()` additionally rejects non-finite floats, so a bad value is a `ValueError` at record time. The check runs in `record()` only, not on the field, so an older run whose `output` extra happens to hold `NaN` still loads. It is serialized through the existing `model_dump_json(exclude_defaults=True)` path in `io/writer.py`, so the SDK and the CLI share one serializer.
  - `output` is an opaque case field, accepted on any schema version. It never affects validation, comparison or decisions. A pre-0.7 run that already has an `output` extra therefore loads unchanged. It is dropped when repeats are collapsed, and no report (terminal, JSON, HTML, Markdown) renders it. The `Report` model never carries a raw `CaseResult`.
  - Errored cases never store output: neither `record_error` cases nor cases turned into errors by a failing evaluator.
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
  - Runs without native evaluators serialize exactly as before, except for the stored `output` when `store_output=True`. That doesn't change the schema version, because older EvalGate already keeps an unknown `output` as an extra and ignores it, so nothing can fail open.
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
- Copying prompts, `case` data or judge explanations into run files, or outputs into run files without `store_output=True`. Outputs never go into reports.

## 7. Release plan

| Release | Scope | Acceptance |
|---|---|---|
| **0.7.0** | `evaluators/` package: interface, built-ins from section 2, `EvalRun(evaluators=, store_output=)` + `record(output=)`, typed `evaluators` manifest field (schema 1.2), `evaluator_set_mismatch` / `_partial`, docs page, `examples/native-evaluators` | An evaluator that raises gives `status: error`, never a pass. A bad output fails the check instead. No output or `case` text appears in `cases.jsonl` unless `store_output=True`, and none appears in any report even then. Runs without native evaluators serialize and compare exactly as in 0.6.0 (except the opt-in `output` field). A changed evaluator version **or argument** is INVALID, and so is a run with native evaluators compared to one without. Duplicate evaluator names are rejected. A run with both repeats and native evaluators round-trips and compares. A sentinel string stored with `store_output=True` is absent from the JSON, HTML, Markdown and terminal reports. A 0.6 run whose cases have an `output` extra loads and decides unchanged. Core has no new dependency. |
| **0.8.0** | `evalgate evaluate` CLI + `evaluators.yaml` | The CLI and SDK give byte-identical `run.json` and `cases.jsonl` for the same outputs and config. Bad config or a bad outputs line exits 2 and writes nothing. |
| **0.9.0** | `[judge]` extra: rubric, `judge()`, cache, `calibrate` | No network without a user callable. Unparseable judge reply gives `status: error`. A cache hit equals a fresh call when the judge is deterministic. Calibration figures are checked against hand-computed examples. |

Each slice gets its own tests, CHANGELOG entry and docs page, following the 0.3.0–0.6.0 pattern.

## 8. Decisions (were open questions)

1. **Storing outputs: yes, opt-in.** `EvalRun(store_output=True)`, as specified in section 3. Off by default, never in reports.
2. **`json_schema`: optional extra.** `evalgate-ai[schema]` installs `jsonschema`. Core stays dependency-free, and the factory raises a clear error when the extra is missing. It uses a pinned validator class (Draft 2020-12), and its version string includes the installed `jsonschema` major version, because results can change between `jsonschema` releases.
3. **`None` results: not allowed.** An evaluator returns `bool` or `float`. Returning `None` is a wrong type, so it's an evaluator error (`status: error`). For scores, a missing value would also trigger `metric_missing`. Put cases that a check doesn't apply to in a category instead.

## 9. 0.7.0 task breakdown

Six tasks, each landing with its own tests. T1 and T3 don't depend on each other and can be built in parallel. Every task must leave the pre-push checks green (ruff, ruff format, mypy, pytest).

```
T1 schema ──▶ T2 pair checks ──┐
                               ├──▶ T5 EvalRun ──▶ T6 docs + release
T3 core ────▶ T4 built-ins ────┘
```

**T1. Schema 1.2 and models** (`models/run.py`, `validation.py`, `io/writer.py`)
- Add `SCHEMA_VERSION_EVALUATORS = "1.2"` and widen the `Literal`.
- Add `RunManifest.evaluators: dict[NonEmptyStr, NonEmptyStr] | None`, left out of the serialized file when unset, the same way as `repeats`.
- Add `CaseResult.output: JsonValue | None`. The defaults are already left out on write.
- Change the repeats check in `validate_run` to accept `"1.1"` or `"1.2"`.
- A manifest that declares `evaluators` with schema `1.0` or `1.1` is rejected with a new code, `evaluators_schema`.
- Tests:
  - a run without evaluators writes the same bytes as 0.6.0;
  - a 0.6 run whose cases have an `output` extra (including `NaN`) loads and decides unchanged;
  - a 1.1 `run.json` with an `evaluators` extra is rejected;
  - repeats with 1.2 are accepted.

**T2. Pair checks** (`validation.py`, `docs/report-format.md`)
- Add `evaluator_set_mismatch` and `evaluator_set_partial`, both reported through `_diag(rules.require_same_evaluator, ...)`, with a message listing which names or versions differ, sorted.
- Tests:
  - identical maps pass;
  - a differing version, an extra name, or only one run declaring evaluators each give an error by default and a warning when `require_same_evaluator: false`;
  - runs without evaluators produce neither code.
- Add both codes to the list of diagnostic codes in `report-format.md`.

**T3. Evaluator core** (new `evaluators/` package, pure, no I/O)
- `Evaluator` is a frozen dataclass with `name`, `version`, `kind` and `fn`. `@check(name=, version=)` and `@score(...)` decorators build one.
- `apply(evaluators, output, case)` returns either `(scores, checks)` or the name of the first evaluator that failed.
  - It enforces return types: a check needs a strict `bool`; a score needs a finite `float`, so `bool`, NaN and `None` are rejected.
  - It catches `Exception`, never `BaseException`.
  - Evaluators run in list order, so the first failing name is deterministic.
- Tests for each of these rules, plus "an exception's text never reaches the result".

**T4. Built-ins** (`evaluators/builtins.py`, `pyproject.toml`)
- Write a helper that makes the version string: `<impl_version>+<first 12 hex of sha256 of the canonical JSON of the arguments>`.
- Build the eight factories from section 2. Each one requires `name=`. Bad arguments (an invalid regex, model or schema, or a `normalize` value outside the allowed set) raise at construction.
- Add `[project.optional-dependencies] schema = ["jsonschema>=4"]`. `json_schema` uses Draft 2020-12 and adds the installed `jsonschema` major version to its version string.
- Tests for each factory:
  - one output that passes and one that fails;
  - the "Raises when" column of the section 2 table;
  - changing an argument changes the version, and the same arguments give the same version;
  - `json_schema` raises a clear error when the extra isn't installed (simulate the missing import).

**T5. `EvalRun` integration** (`io/writer.py`)
- `EvalRun(evaluators=, store_output=)`. Duplicate evaluator names raise `ValueError` at construction. The `evaluators` map in the manifest is sorted, and the writer picks 1.2 / 1.1 / 1.0 as section 4 describes.
- `record(output=, case=)`:
  - runs `apply`;
  - a clash with a score or check the caller passed raises `ValueError`;
  - if an evaluator fails, the case becomes `status: error` with the message `evaluator <name> failed`, and no output is stored;
  - with `store_output=True`, `output` is required on `ok` records and is checked for non-finite floats.
- Tests:
  - a sentinel output stored with `store_output=True` is absent from the JSON, HTML, Markdown and terminal reports;
  - repeats plus evaluators round-trip and compare;
  - an evaluator that raises gives `status: error`, never a pass;
  - an output that fails a check gives `False`, not an error;
  - a changed evaluator argument between baseline and candidate is INVALID.

**T6. Docs and release** (`__init__.py`, `docs/evaluators.md`, `examples/native-evaluators`, `README.md`, `CHANGELOG.md`, `_version.py`)
- Export the public API (`check`, `score`, `Evaluator` and the built-ins) from `evalgate.evaluators`.
- Write a docs page in the style of the existing ones, and an example run pair that FAILs for a documented reason. Register the example in `test_examples.py`.
- Add a CHANGELOG 0.7.0 entry with Added, Changed (the new diagnostic codes, schema 1.2, `evaluators` reserved in `run.json`) and Testing. Bump the version to 0.7.0.
- Release review before tagging, as for 0.5.0 and 0.6.0.

**Out of 0.7.0:** the `evalgate evaluate` CLI (0.8.0), LLM judges (0.9.0), and pairwise judging (deferred).

## 10. 0.8.0 task breakdown

The goal is `evalgate evaluate`: score an outputs file with built-in evaluators from a YAML config, for teams that don't use Python. There are four tasks, each landing with its own tests, and the pre-push checks must stay green. They run in order: T1 → T2 → T3 → T4.

### Decisions this plan makes (defaults; object before T1 starts)

- **The manifest lives in the config,** as an `ImportManifest` block (the same shape as `mapping.yaml`). `--run-id` overrides `manifest.run_id`. The one config file fully describes how the run was scored.
- **Only built-ins can be used from YAML.** `matches_model` is left out, since it needs a Python class. Custom `@check` / `@score` evaluators also stay SDK-only (section 3: no code loading from YAML).
- **`case` for an evaluator** is the outputs line without its `output`, `status`, `error`, `metrics` and `repeat`. That means `{case_id, expected, metadata}`, so `exact_match()` and `numeric_close()` read `expected` by default.
- **Strict input everywhere.** Unknown keys in the config or an outputs line are rejected, so a typo can't silently drop a check. Every bad line is reported, and nothing is written unless every line is valid.
- **No output text in errors.** Diagnostics name the line number and field, never the value (they use the existing `_schema_errors` helper, which reports only the location and the message).
- **One engine.** `evalgate.evaluate_outputs(...)` is the SDK function, and the CLI is a thin wrapper around it. Both write through `write_rows`, and so through `EvalRun`, so CLI and SDK output are byte-identical.

### Config shape (`evaluators.yaml`)

```yaml
version: 1
manifest:
  dataset_id: support-suite
  dataset_version: v3
  evaluator_version: native-v1
  # run_id, application, application_version optional
store_output: false            # optional
evaluators:
  - type: json_valid
    name: is_json
  - type: length
    name: concise
    max: 300
  - type: json_schema
    name: answer_schema
    schema_file: answer.schema.json   # relative to this config; or inline `schema:`
  - type: not_contains
    name: no_ai_disclaimer
    text: as an AI
    ignore_case: true
```

**T1. Config model and loader** (`models/evaluators_config.py`, `io/evaluators_config.py`)
- A strict Pydantic config model with `extra="forbid"`. Each `type` is one of the YAML-capable built-ins, plus that factory's arguments.
- `schema` and `schema_file` are mutually exclusive. `schema_file` is resolved relative to the config file, must stay inside the config's directory (no `..` escape), and must be JSON.
- `load_evaluators_config(path)` returns the manifest, the built `Evaluator`s and `store_output`. Building happens by calling the factories, so argument errors and duplicate names surface when the config loads.
- YAML is read with the existing duplicate-key-rejecting loader.
- **New diagnostic codes (exit 2):**
  - `evaluators_yaml`: the file can't be read, or the YAML can't be parsed;
  - `evaluators_config`: a schema error, an unknown type, bad arguments, a duplicate name, or a missing `jsonschema` extra.
- **Tests:**
  - every built-in round-trips from YAML;
  - the same config gives the same versions as the equivalent Python factories;
  - unknown keys, an unknown type, `matches_model`, bad arguments, duplicate names, `schema` together with `schema_file`, a `schema_file` that escapes the directory or is missing, duplicate YAML keys, and a missing extra each give `evaluators_config` or `evaluators_yaml`.

**T2. Outputs reader and engine** (`io/evaluate.py`, `io/importer.py`)
- A strict model for an outputs line: `case_id`, `status` (`ok`/`error`), `output` (required if ok, forbidden if error), `error` (only if error), and optional `expected`, `metadata`, `metrics` and `repeat`.
- **New diagnostic codes:** `outputs_parse` for a line that isn't JSON (by line number only), and `outputs_record` for a schema error on a line.
- `write_rows` gains `evaluators=` and `store_output=`, and passes each row's `output` and `case` to `record()`. Existing importers are unchanged: their default is no evaluators.
- **`evaluate_outputs(source, config, out, *, run_id=None, overwrite=False) -> ImportSummary`,** exported from `evalgate`.
  - An evaluator error still becomes `status: error`, as in the SDK.
  - The summary adds a count of evaluator errors.
- **Tests:**
  - the SDK and `evaluate_outputs` write byte-identical `run.json` and `cases.jsonl` for the same inputs;
  - every bad line is reported, and nothing is written;
  - no output text appears in any diagnostic (checked with a test string);
  - `status: error` lines are recorded as errors;
  - repeats work;
  - `store_output` from the config works;
  - an existing run without `--overwrite` gives `import_exists`, the code shared with the importers;
  - `expected` reaches `exact_match`.

**T3. CLI** (`cli.py`)
- `evalgate evaluate OUTPUTS.jsonl --config evaluators.yaml --out DIR [--run-id ID] [--overwrite]`, wrapped in `_fail_closed` like the other commands. On success it prints a summary (cases, repeats, evaluator errors) and exits 0. Any diagnostic exits 2.
- Add an `if __name__ == "__main__"` entry point so `python -m evalgate.cli` works. That was a pre-existing gap noticed in the 0.7.0 review.
- **Tests:**
  - the exit codes (0 and 2);
  - the CLI writes the same files as `evaluate_outputs`;
  - `--run-id` overrides the config;
  - the help text lists the command.

**T4. Docs and release** (`docs/evaluators.md`, `README.md`, `examples/`, `CHANGELOG.md`, `_version.py`)
- Add a CLI section to `docs/evaluators.md` covering the config reference, the outputs line format and the diagnostics. Add the new codes to `report-format.md`. Update the README snippet and the "Not yet included" list.
- Add `outputs.jsonl` and `evaluators.yaml` to `examples/native-evaluators`, describing the same scenario. A test checks that the CLI path gives the same decision as the committed runs.
- Add a CHANGELOG 0.8.0 entry and bump the version to 0.8.0. Mark the design doc's status as 0.8.0 implemented.

**Out of 0.8.0:** CSV outputs files (JSONL only), `matches_model` and custom code from YAML, LLM judges (0.9.0), and pairwise judging (deferred).
