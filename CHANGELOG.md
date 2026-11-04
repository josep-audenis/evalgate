# Changelog

All notable changes to `evalgate-ai`. Versions follow [Semantic Versioning](https://semver.org/). Before 1.0, a minor version may change behaviour; anything that does is listed under **Changed**.

## 0.2.0

Statistics release (SPEC Phase 4). By default it still makes the same PASS/FAIL/INVALID decisions as 0.1.0. The new statistical rules are opt-in, and they can only make a decision stricter, never looser.

### Added

- **Small-sample warning.** When fewer than 30 cases are `ok` in both runs, the report gets a `small_sample` warning. A new policy option, `comparison.min_compared_cases: N`, makes a smaller sample INVALID (`too_few_cases`).
- **Confidence intervals** for every metric's mean change (`comparison.metrics[].ci`). Up to 2000 cases use a paired percentile bootstrap with a fixed seed, so results are deterministic. Above that, a normal approximation is used.
- **Conclusive rules.** Metric rules report `conclusive`, which is true when the whole interval lies on one side of the threshold. New policy block: `statistics: {confidence: 0.95, require_conclusive: false}`. With `require_conclusive: true`, an inconclusive pass becomes a FAIL.
- **Paired check statistics** (`comparison.check_stats`): per check, counts of newly failing and newly passing cases, plus a one-sided exact McNemar p-value. New opt-in rule: `checks.fail_on_significant_regression: true`. It must be combined with another rule, because with only a few flips it can never fail.
- The terminal and HTML reports show the intervals and check statistics. `docs/report-format.md` documents the methods, assumptions and limitations.

### Changed

- **Terminal metric table:** numbers are shown to 4 significant digits, and the `direction` column is now `goal` (`max`/`min`), so the table fits in 80 columns. The JSON report keeps full precision, but scripts that parse the terminal output may need updating.
The JSON report is still `report_version: "1"`, because all new fields are additions.

## 0.1.0

First public release: baseline vs candidate comparison, YAML release policies, PASS/FAIL/INVALID with exit codes 0/1/2, the `evalgate init` / `validate` / `compare` CLI, the `EvalRun` SDK, and terminal, JSON and standalone HTML reports.
