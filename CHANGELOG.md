# Changelog

All notable changes to `evalgate-ai`. Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change behaviour; anything that does is listed under **Changed**.

## 0.8.0

`evalgate evaluate`: score outputs with the built-in evaluators from a YAML config, without writing Python (SPEC Phase 6, second slice). Decisions, exit codes and the JSON report are unchanged from 0.7.0.

### Added

- **`evalgate evaluate OUTPUTS.jsonl --config evaluators.yaml --out DIR [--run-id ID] [--overwrite]`** (also `evalgate.evaluate_outputs`). See [docs/evaluators.md](docs/evaluators.md#without-python-evalgate-evaluate).
  - **`OUTPUTS.jsonl`:** one case per line: `case_id`, `status` (`ok`/`error`), `output` (when ok), and optionally `expected`, `scores`, `metrics`, `metadata`, `repeat`, `error`. Evaluators see `{case_id, metadata, expected}` as the case.
  - **`evaluators.yaml`:** a `manifest` block (as in an import mapping), `store_output`, and a list of built-ins by `type` and `name` with the same arguments as in Python. `json_schema` takes an inline `schema` or a `schema_file` inside the config's directory. `matches_model` and custom evaluators stay Python-only: the config never loads code.
  - **One engine:** the command writes through `EvalRun`, so its `run.json` and `cases.jsonl` are byte-identical to recording the same outputs with the SDK, and the evaluator versions match.
  - **Fail closed:** unknown keys and wrong types are rejected; every bad line or config entry is reported, and nothing is written unless everything is valid (exit 2). Messages name a line and a field, never an output's value. Duplicate keys, non-finite numbers (`NaN`, `1e999`), a `scores`/`metrics` name that matches an evaluator, and `repeat` on only some lines are rejected.
  - **New diagnostic codes:** `evaluators_yaml`, `evaluators_config`, `outputs_parse`, `outputs_record`.
  - The summary reports how many cases became `status: error` because an evaluator failed.
- **`python -m evalgate.cli`** now runs the CLI (it did nothing before).
- **`examples/native-evaluators`** gains `evaluators.yaml`, `answer.schema.json` and `outputs-*.jsonl`, the no-Python version of the same scenario.

### Changed

- **`ImportSummary`** gains `evaluator_errors` (0 for imports). `EvalRun` gains an `evaluator_errors` counter.
- **`import_exists` messages** (`evalgate import` and `evaluate`) now name `--overwrite`, and an `--out` that is (or is under) a file says so instead of showing a raw OS error.

### Testing

- New tests cover the config loader (including hand-written YAML edge cases), the outputs reader, the CLI, and the example via the CLI path. A test checks that the CLI and the SDK write byte-identical files, also with repeats and `store_output`.

## 0.7.0

Native evaluators (SPEC Phase 6, first slice): EvalGate can now score outputs itself while you record a run. It's optional; runs without native evaluators get the same decisions as in 0.6.0, and are written byte for byte the same.

### Added

- **Native evaluators.** `EvalRun(..., evaluators=[...])` with `record(case_id, output=..., case=...)` runs evaluators on each output and writes their results as checks and scores. See [docs/evaluators.md](docs/evaluators.md).
  - **Built-ins** (`evalgate.evaluators`): `json_valid`, `matches_model` (Pydantic), `json_schema` (Draft 2020-12, offline, with the new optional `evalgate-ai[schema]` extra), `regex`, `contains`, `not_contains`, `exact_match`, `length`, `numeric_close`. Each needs a `name=`. Bad arguments raise when the evaluator is created.
  - **Custom evaluators:** `@check(name=, version=)` and `@score(name=, version=)` decorators.
  - **Fail closed:** an evaluator that raises or returns the wrong type records the case as `status: error` (`evaluator <name> failed`, with no exception text), never as a pass. A bad output just fails the check.
  - **Versions:** `run.json` declares each evaluator's name and version. A built-in's version includes a hash of its arguments, so changing `length(max=...)` changes it.
  - `case` (the dataset record) is passed to evaluators and never written.
  - Duplicate evaluator names raise when `EvalRun` is created; an evaluator name that clashes with a score or check you pass yourself raises when recording.
  - Lower-level API: `Evaluator`, `apply` and `Applied` are exported too.
- **Stored outputs.** `EvalRun(..., store_output=True)` writes each `ok` case's output to `cases.jsonl` as an `output` field, for debugging. Outputs must be JSON and finite. They never appear in reports or affect decisions.
- **New diagnostics:** `evaluator_set_mismatch` and `evaluator_set_partial` (follow `comparison.require_same_evaluator`: INVALID by default), and `evaluators_schema` (INVALID).
- **`examples/native-evaluators`:** a candidate whose average quality improves while one answer stops being valid JSON.

### Changed

- **Schema 1.2.** Runs with native evaluators use `"schema_version": "1.2"`, which includes everything in 1.1 (repeats are accepted with 1.2). EvalGate 0.6 rejects 1.2 files rather than ignoring the evaluator list.
- **`evaluators` in `run.json` is now a reserved name.** A 1.0 or 1.1 run that used it as custom metadata is INVALID (`evaluators_schema`), or rejected when loaded if it isn't a name-to-version map.
- **`output` on a case is now a typed field** (any JSON value). Run files that already used it load and decide unchanged, but in the SDK, `CaseResult(output=...)` with a value that isn't JSON now raises.
- **`repeat_schema`** now says "1.1 or later".
- **`EvalRun(evaluators=..., store_output=...)`** are now real parameters (0.6 stored any extra keyword as run metadata), and `store_output` must be a bool.

### Testing

- New tests cover:
  - schema 1.2;
  - the evaluator core and every built-in, including that no network access happens;
  - the evaluator set checks;
  - `EvalRun` integration, including a check that a stored test string reaches none of the four report formats;
  - the new example, including a check that its committed runs match its generator.

## 0.6.0

Markdown summaries for pull requests and CI job pages, and a Langfuse recipe. Decisions, exit codes and the JSON report are unchanged from 0.5.0.

### Added

- **Markdown summary.** `evalgate compare ... --markdown report.md` (also `Report.to_markdown`) writes a GitHub-flavored summary for pull request comments and CI job summaries: the decision, failed rules first, metrics with confidence intervals, diagnostics, and collapsed lists of regressed cases and newly failing checks (first 10 each). Text from runs is shown as inline code, so it can't break tables, add HTML, links or math, or @mention people and reference issues; long values and lists are capped so the summary stays under GitHub's 65,536-character comment limit. [docs/ci.md](docs/ci.md) shows a job-summary step and an optional PR comment.
- **[docs/langfuse.md](docs/langfuse.md):** writing a Langfuse experiment (Python SDK) as an EvalGate run with `EvalRun`, since Langfuse has no local export of experiment results. The recipe records failed tasks and evaluators as errors, which Langfuse only logs and leaves out of its results. Checked against Langfuse Python SDK 5.2; a test pins the recipe's code.
- **Python 3.14** in the package classifiers.

### Testing

- The test suite passes on Python 3.11, 3.13 and 3.14, and mypy passes targeting 3.11 to 3.14. JSON, HTML and terminal reports and imported runs are byte-identical across 3.11, 3.13 and 3.14. A new test pins exact bootstrap interval bounds, so a Python version that changed the resampling would fail.

## 0.5.0

DeepEval adapter: with promptfoo, two external result formats now import without rewriting records (SPEC Phase 5 acceptance).

### Added

- **DeepEval adapter.** `evalgate import test_run.json --from deepeval --out DIR --dataset ... --dataset-version ... --evaluator-version ...` (also `evalgate.import_deepeval`) imports a DeepEval test run file (`test_run_<timestamp>.json` from a results folder, or `.deepeval/.latest_run_full.json`). Single-turn and conversational test cases are supported. See [docs/deepeval.md](docs/deepeval.md).
  - Metric scores become scores; metric pass/fail (for metrics with a threshold) and test case pass become checks; `completionTime`, `tokenCost` and token counts become `latency_ms`, `cost` and `tokens`; scalar metadata is kept.
  - Case ids come from the test case `name`, or `--case-id metadata.<key>`. Position-based default names (`test_case_<n>`, `conversational_test_case_<n>`, pytest ids like `test_bot[test_case0]` or `test_bot[test_case0-gpt]`) and duplicate ids are rejected.
  - A test case with an errored metric (an `error`, or a threshold without a score as `deepeval test run` records it) becomes `status: error` with a fixed message. Flaky metrics get a score but no check. Inputs, outputs, contexts, turns, reasons and error messages are never copied.

### Changed

- **`evalgate import --case-id`** has no fixed default in the CLI any more: each adapter applies its own (`description` for promptfoo, `name` for DeepEval). Behaviour for promptfoo is unchanged. `--case-id description` together with `--mapping`, which 0.4.0 silently ignored, is now rejected (exit 2) like any other `--case-id` with `--mapping`.

## 0.4.0

Per-category limits, documented statistics (SPEC Phase 4 complete) and a promptfoo adapter. Policies without category limits get the same decisions as in 0.3.0.

### Added

- **Per-category limits.** `max_category_regression_abs` and `max_category_regression_pct` on a metric apply the limit to each `metadata.category` separately, with rules named `metrics.<name>.categories.<category>.max_category_regression_abs` / `_pct`. A regression confined to one category now fails even when the overall mean improves.
  - **Fail closed:** a category limit when no compared case has a text `metadata.category` is INVALID (`category_missing`), and so is a compared case whose category is not non-empty text (`category_type`). Uncategorized compared cases give a `category_partial` warning. A percent limit on a category whose baseline mean is 0 follows the same `pct_undefined` rule as metrics.
  - `statistics.require_conclusive` applies to category rules too.
- **Category intervals.** `comparison.categories[]` gains `delta_pct` and `ci` (same shape as `metrics[].ci`). The HTML report shows the interval.
- **promptfoo adapter.** `evalgate import results.json --from promptfoo --out DIR --dataset ... --dataset-version ... --evaluator-version ...` (also `evalgate.import_promptfoo`) imports a `promptfoo eval -o` file (results format version 3) without a mapping. See [docs/promptfoo.md](docs/promptfoo.md).
  - Overall score and named metrics become scores; test pass and per-metric assertion results become checks; latency, cost and tokens become metrics (left out for cached responses); test metadata is kept; `--repeat` runs become repeated runs.
  - Case ids come from the test description by default (`--case-id vars.<name>` / `metadata.<name>`); missing or duplicate ids are errors. Files with several providers or prompts need `--provider` (label, then id) / `--prompt` (prompt id or prefix); prompt text is never shown.
  - Provider and grader errors become `status: error` cases with a fixed message; an unknown `failureReason` is rejected. Prompts, outputs and error messages are never copied, and vars only when chosen as the case id.
- **[docs/statistics.md](docs/statistics.md):** methods, assumptions and limits of the intervals, McNemar test, category limits and sample-size checks.

### Changed

- **JSON report:** the echoed policy shows the new `max_category_regression_abs` / `_pct` fields of each metric (as `null` when unset), and `comparison.categories[]` has the new `delta_pct` and `ci` fields. Both are additions; nothing was removed or renamed.
- **`evalgate import`:** `--mapping` is no longer a required option, since `--from promptfoo` replaces it. Passing neither still exits 2, now with an `INVALID` message naming both options.
- **Fail-closed edge case:** each category now gets a confidence interval, so metric values near the float limit inside one category can make a comparison INVALID (`numeric_overflow`) even without category limits. 0.3.0 decided such inputs on the overall metric alone.

## 0.3.0

Repeated runs and importing (SPEC Phase 4 completion, Phase 5 start). Runs without repeats get the same decisions as in 0.2.0.

### Added

- **Repeated runs.** Evaluate each case several times per version, so run-to-run noise in nondeterministic systems is measured instead of mistaken for change.
  - **Format:** add `"repeat": 0..k-1` to each line of `cases.jsonl`, use `"schema_version": "1.1"`, and optionally declare `"repeats": k` in `run.json`.
  - **Combining:** each case's repeats are combined into one case. Metrics are averaged, a check passes only if it passes in every repeat, and any errored repeat makes the case an error.
  - **Analysis:** means, intervals, rules, McNemar and sample-size checks then count **cases, never repeats** (SPEC §9.2).
  - **Fail closed:** missing, duplicated or mixed repeats, and repeats of a case that report different fields, are INVALID (`repeat_incomplete`, `duplicate_case_id`, `repeat_mixed`, `repeat_inconsistent`, `repeat_schema`). A `repeat_metadata` warning flags repeats with different metadata.
  - **Report:** `comparison.repeats` gives k per run, per-check pass rates and each metric's within-case SD (the noise level). The terminal and HTML show them.
  - **SDK:** `EvalRun(..., repeats=k)` with `record(..., repeat=i)` and `record_error(..., repeat=i)`.
  - Design: [docs/design/repeated-runs.md](docs/design/repeated-runs.md).
- **Generic importer.** `evalgate import SOURCE --mapping mapping.yaml --out DIR [--run-id ID] [--overwrite]` (also `evalgate.import_results`) converts CSV, JSON or JSONL results from other tools into a run directory, using a strict mapping file.
  - **Fail closed:** values must be valid numbers or explicit true/false; rows must match the CSV header; duplicate columns or keys and mapped fields absent from every record are errors. Every bad record is reported, and no run files are written unless all records convert.
  - **Privacy:** only mapped fields are imported, so unmapped prompts and outputs are never copied.
  - **Diagnostic codes:** `import_format`, `import_run_id`, `import_field_missing`, `import_record`, `import_exists`, `import_parse`, `mapping_yaml`. The SDK also exports `ImportSummary`.
  - Docs: [docs/importing.md](docs/importing.md).
- **New examples:** `examples/repeats` (a flaky safety check behind an unchanged average) and `examples/import-csv`.

### Changed

- **`repeat` (on a case) and `repeats` (in `run.json`) are now reserved names.** A 0.2.0 run file that used them as custom metadata is now rejected or INVALID, never misread.
- **Run files may declare `schema_version: "1.1"`** even without repeats. 0.2.0 rejected that.
- **`evalgate validate`** now reports cases and repeats separately ("6 cases x 3 repeats").
- **A failed or interrupted `EvalRun`** (including a failed import) now deletes its `.cases.jsonl.partial` file. Any previous run in that directory is still left untouched.

The JSON report is still `report_version: "1"`, because `comparison.repeats` is an added field.

## 0.2.0

Statistics release (SPEC Phase 4). The new statistical rules are opt-in, and they can only make a decision stricter, never looser. Existing 0.1.0 policies are still accepted. With them, decisions are the same as in 0.1.0, with one fail-closed exception listed under **Changed**.

### Added

- **Small-sample warning.** When fewer than 30 cases are `ok` in both runs, the report gets a `small_sample` warning. A new option, `comparison.min_compared_cases: N`, makes a smaller sample INVALID (`too_few_cases`) and replaces the warning.
- **Confidence intervals** for every metric's mean change (`comparison.metrics[].ci`, including percent bounds). Up to 2000 cases use a paired percentile bootstrap with 1000 resamples and a fixed seed, so results are deterministic. Above that, a normal approximation is used.
- **Conclusive rules.** Metric rules report `conclusive`, which is true when the whole interval lies on one side of the threshold. A rule over a single case is never conclusive.
- **New policy block:** `statistics: {confidence: 0.95, require_conclusive: false}`.
  - `confidence` must be between 0.5 and 0.999.
  - With `require_conclusive: true`, an inconclusive pass becomes a FAIL, which means one-case comparisons always FAIL.
- **Paired check statistics** (`comparison.check_stats`): per check, the cases compared, failures in each run, newly failing and newly passing counts, and a one-sided exact McNemar p-value.
- **New opt-in rule, `checks.fail_on_significant_regression: true`:** FAIL when p < 1 − `statistics.confidence`. It must be combined with another rule, because with only a few flips it can never fail.
- The terminal shows the interval per metric, a "CI:" note and a "check flips" section. The HTML report gets an interval column and a Checks table. `docs/report-format.md` documents the methods, assumptions and limitations.

### Changed

- **Terminal metric table:** numbers are shown to 4 significant digits, and the `direction` column is now `goal` (`max`/`min`), so the table fits in 80 columns. Scripts that parse terminal output may need updating; the JSON report keeps full precision.
- **Metric rule messages** now end with the interval of the regression, and with "- inconclusive" when it spans the threshold. Messages are for people and shouldn't be parsed.
- **Runs with fewer than 30 compared cases**, including the bundled examples, now report a `small_sample` warning, so `diagnostics` is no longer empty for them.
- **Fail-closed change:** inputs whose confidence interval is not finite are now INVALID (`numeric_overflow`). In practice this means values near the float limit, or a baseline mean so close to 0 that the percent bounds overflow. Version 0.1.0 decided such inputs on the point estimate alone.

The JSON report is still `report_version: "1"`, because every change is an added field: `ci`, `conclusive`, `check_stats`, and the new keys in the echoed `policy`.

## 0.1.0

First public release: baseline vs candidate comparison, YAML release policies, PASS/FAIL/INVALID with exit codes 0/1/2, the `evalgate init` / `validate` / `compare` CLI, the `EvalRun` SDK, and terminal, JSON and standalone HTML reports.
