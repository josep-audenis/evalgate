# Changelog

All notable changes to `evalgate-ai`. Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change behaviour; anything that does is listed under **Changed**.

## 0.13.0

An interactive HTML report and a history page (SPEC Phase 7, fourth slice). Decisions, exit codes and the JSON report are unchanged from 0.12.0.

### Added

- **Sortable and filterable HTML report.** The report stays one offline file. See [docs/report.md](docs/report.md).
  - Click a column header to sort: numbers by their full value, empty and `n/a` cells last, text in the same order in every browser.
  - Case-level tables (case lists, errors, failure groups, categories) get a filter box.
  - Every section folds.
  - Without JavaScript the page is still complete.
- **A Content Security Policy in the HTML report.** Only the embedded viewer script runs, pinned by its hash, and nothing can be fetched, framed or submitted.
- **New HTML sections,** so the page shows everything the JSON does:
  - the baseline's errored case ids (and the candidate's, now as a filterable table rather than a list);
  - the candidate's failing checks;
  - failure groups (by check × category, by check, by category).
- **`evalgate history report OUT.html`:** a standalone page for one selection of a history store. It holds a server-drawn SVG trend chart per metric (gaps for missing values), the entries table and the recurring failures. It takes `--kind`, `--dataset`, `--application`, `--last` and a repeatable `--path`. See [docs/history.md](docs/history.md#the-history-page).
- **`examples/history`** also writes `history.html`.

### Changed

- **The HTML report (`--report`) uses the EvalGate look:** a monospace terminal style with a dark theme by default and a light one when the OS is light. The decision is a large `decision: FAIL` line with its exit code, and warnings are yellow. It is still one offline file: Martian Mono is used only where it is installed, otherwise the system monospace font.
- **The HTML report now contains one script.** It used to have none. If your environment strips scripts, the report still works: only sorting and filtering are lost.

### Testing

- New tests cover:
  - the CSP hash, computed independently from the output;
  - parsed-markup checks: one script, no inline handlers, only `data:` references, no `url(` in style attributes;
  - forbidden APIs in the script;
  - every new section matching the JSON row by row;
  - full-precision sort keys and hostile names;
  - a golden file, and identical bytes across processes;
  - the sort comparator, run in Node when it's installed;
  - exact SVG coordinates (gaps, one point, equal values), one shared selection, and the history page's CLI exit codes.

## 0.12.0

Failure groups and recurring failures (SPEC Phase 7, third slice). Reporting only: decisions and exit codes are unchanged from 0.11.0, and the report gains one additive field.

### Added

- **`comparison.failure_groups` in the JSON report:** the candidate's failing checks on compared cases, grouped by check, by category (the baseline's) and by both, plus an `uncategorized` count. See [docs/report-format.md](docs/report-format.md#comparisonfailure_groups).
  - Each failing `(case, check)` pair is `new` (passing in the baseline), `persistent` (failing there too) or `no_baseline` (the baseline case has no such check). Every group satisfies `failing == new + persistent + no_baseline`.
  - Nothing is truncated. Reports written before 0.12 (no field) still load.
- **Terminal and Markdown reports** show the top 5 groups, falling back to by-check groups when no failing case has a category. They note how many failing pairs had no category. Nothing is shown for an INVALID report. The HTML report gets the groups with the 0.13 viewer.
- **`evalgate history failures`** lists `(case, check)` pairs that keep failing across recorded entries: `failing`, `of`, `first`, `last` and the current `streak`. It takes `trend`'s filters plus `--min` (default 2; below 1 exits 2) and `--json`. See [docs/history.md](docs/history.md).
- **`examples/history`** records a fourth entry (a patch release that still fails), so `history failures` has something to show.

### Changed

- **History entries are written as `history_version: 2`.** Version 1 lines still read, and a store may mix them.
  - Run entries carry `case_ids` (their ok cases).
  - Compare entries take their failing pairs from compared cases only. Version 1 entries could include candidate-only cases when `require_same_cases: false`.
  - EvalGate 0.10 and 0.11 report version 2 lines as an unsupported `history_version`.

### Testing

- New tests cover:
  - exact groups and their invariant;
  - consistency with `check_stats` and `newly_failing_checks`;
  - compared cases only; baseline, odd and missing categories;
  - repeats collapsed before grouping;
  - old reports loading;
  - renderer top 5, fallback, INVALID and hostile names (Markdown tables stay intact);
  - version 2 rules (`case_ids` sorted, unique and covering the failures), mixed stores;
  - hand-computed `failing`, `of`, `first`, `last` and `streak`, including streaks ended by missing or errored cases;
  - the CLI's exit codes and JSON.

## 0.11.0

Several candidates in one command (SPEC Phase 7, second slice). With one candidate, `compare` behaves exactly as in 0.10.0.

### Added

- **`evalgate compare BASELINE CANDIDATE...`** judges each candidate against the same baseline with the same engine and policy. Each report is the one a two-run compare would give. See [docs/candidates.md](docs/candidates.md).
  - Exit 2 if any candidate is INVALID, else 1 if any FAILs, else 0.
  - With several candidates, `--json`, `--report` and `--markdown` paths must contain `{n}` (the candidate's position), so one file is written per candidate. A missing `{n}` exits 2 before anything runs.
  - **`--summary PATH`:** one JSON file with every candidate's status, the decision exit code and the ranking. No timestamps.
  - **`--rank-by NAME`:** ranks candidates on one policy metric, in its declared direction. It is informational only: no aggregate score, never a decision. FAIL candidates are ranked and shown as FAIL; INVALID ones are left out; ties keep order. A warning is printed when the ranked means cover different numbers of cases.
  - **`--history`:** records one entry per candidate, in order.
- **SDK:** `evalgate.compare_many(baseline, candidates, policy="evalgate.yaml", *, rank_by=None)` returns a `MultiResult` (reports, exit code, ranking, `summary()`).
- **`examples/multi-candidate`:** three candidates; the best-quality one FAILs on safety but ranks first on quality.

### Changed

- **A failed artifact write in `compare`** (`--json`, `--report`, `--markdown`) now prints the decision first and then exits 2. Before, it printed only an internal error, also with exit 2. `--summary` behaves the same way.
- `compare --help` names the arguments `BASELINE CANDIDATE...`.

### Testing

- New tests cover:
  - each report equal to a two-run compare, and the exit code for every PASS / FAIL / INVALID mix;
  - ranking direction, ties, exclusions and case counts;
  - the input guards, and summary determinism;
  - `{n}` templating (repeated, other braces, directory parts), and usage errors before any I/O;
  - write failures on any writer, and history failing partway;
  - a single candidate's artifacts, terminal output and history being identical to a plain compare;
  - escaping, and the example.

## 0.10.0

Local history (SPEC Phase 7, first slice): keep a record of release decisions and runs, and see how metrics move across releases. It's opt-in; decisions, exit codes and the JSON report are unchanged from 0.9.0 unless `--history` is passed (and see the console encoding fix below).

### Added

- **`evalgate compare … --history DIR [--label TEXT]`** appends a `compare` entry to a local store after the decision and the other artifacts. See [docs/history.md](docs/history.md).
  - Every status is recorded, INVALID included.
  - The full report is kept as `reports/<id>.json`, byte-identical to `--json`, with its SHA-256 in the entry.
  - If the store can't be written, the decision is still printed and the command exits 2.
- **`evalgate history add RUN_DIR`** records a `run` entry: identity, case and error counts, score and metric means, check pass rates, and failing checks (repeats collapsed). An INVALID run isn't recorded.
- **`evalgate history list / show ID / trend PATH`**, each with `--json`.
  - `trend` never mixes entry kinds (`--kind run|compare`).
  - It filters with `--dataset`, `--application` and `--last`, and warns when its points span more than one dataset.
- **SDK:** `evalgate.record_report`, `record_run`, `read_history`, `load_entry_report`; `recorded_at=` makes stores reproducible.
- **The store:**
  - It is append-only, strict and line-by-line. Ids increase, gaps are allowed, and the next id is the last + 1. Appends rewrite the index atomically.
  - It never holds outputs.
  - It never picks a baseline or feeds a policy.
- **New diagnostic codes:** `history_missing`, `history_parse`, `history_id`, `history_record`, `history_write`.
- **`examples/history`:** builds a store from the quickstart runs.

### Changed

- **Files EvalGate writes use LF line endings on every OS**, with no more CRLF on Windows: `--json`, `--report`, `--markdown`, `run.json`, `cases.jsonl` and `evalgate init`. The same inputs now give the same bytes everywhere.
- **Atomic writes use a per-process temporary file,** and remove it if the write fails.
- **The CLI no longer crashes** when printing characters that the console encoding can't show (for example a non-ASCII `run_id` piped to a file on Windows). They are replaced, and the exit code is the decision's.

### Testing

- New tests cover:
  - summaries computed by hand (repeats, sparse paths, compared cases only, INVALID reports);
  - the strict entry model;
  - id allocation after pruning;
  - every malformed-line kind, including Unicode line separators inside strings;
  - atomic writes and their failure paths, the report hash, and privacy sentinels;
  - every CLI command and exit code, including a store failure after PASS and after FAIL;
  - trend filters;
  - an engine-isolation scan.

## 0.9.0

LLM judges and judge calibration (SPEC Phase 6, third slice). Judges are optional, Python-only and stdlib-only; decisions, exit codes and the JSON report are unchanged from 0.8.0.

### Added

- **`Rubric` and `judge()`** in `evalgate.evaluators`. `judge(rubric, complete, *, name, model_id, cache_dir=None)` renders a versioned prompt template for each output, calls `complete`, your own model client, and records a check or a score. See [docs/judges.md](docs/judges.md).
  - **No network from EvalGate,** no provider SDK and no extra: `complete` is your function. The comparison engine never references the judge (enforced by a test).
  - **Templates** use `string.Template`: `$output` plus top-level `case` fields; only the fields used are read, in one pass. A literal dollar sign is `$$`.
  - **Strict replies:** exactly `{"pass": bool}` or `{"score": number}` (inclusive `scale`), with an optional `reason` that is discarded. Anything else, or an exception from `complete`, makes the case `status: error`, never a default value.
  - **Versions** are `<rubric.version>+<model_id>+<hash>`. The hash covers the template, kind, scale, rubric version and model id, so changing any of them makes a comparison INVALID.
  - **Opt-in cache** (`cache_dir=`): values only, keyed by the judge version and rendered prompt. Malformed entries are misses, write failures are ignored, errors are not cached.
- **`evalgate calibrate RUN_DIR --labels labels.jsonl [--json OUT]`** (also `evalgate.io.labels.calibrate_run`). Measures agreement between any check or score and human labels: accuracy and Cohen's kappa for checks, MAE and Spearman for scores. Reporting only: exit 0 with a report, 2 on bad input, never 1. Repeats are pooled; errored rows are excluded and counted; a warning prints under 30 labelled cases.
  - **New diagnostic codes:** `labels_parse`, `labels_record`, `labels_unknown`, `labels_empty`.
- **`examples/judge`**: a fake offline judge, a FAILing baseline/candidate pair, `labels.jsonl` and the committed calibration report.

### Changed

- `write_json_report` accepts any report model (used for the calibration report).
- Strict JSON parsing is shared (`evalgate._json.loads_strict`). The `schema_file` of `evaluators.yaml` now rejects out-of-range numbers such as `1e999` when it is read, with a clearer message. It was already rejected, later, by `json_schema`.

### Testing

- New tests cover rubric construction, every malformed reply, rendering rules, version sensitivity with a pinned version string, the cache (poisoning, atomic writes, failures), every calibration figure against hand-computed values, the labels reader, the CLI, and the example's generator parity.

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
