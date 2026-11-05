# Changelog

All notable changes to `evalgate-ai`. Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change behaviour; anything that does is listed under **Changed**.

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
