# Changelog

All notable changes to `evalgate-ai`. Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change behaviour; anything that does is listed under **Changed**.

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
