# JSON report format (`report_version: "1"`)

`evalgate compare --json report.json` and `Report.to_json(path)` write the same document. The HTML report and terminal summary are views of this same data.

## Stability

- `report_version` changes only on **breaking** changes: removing or renaming a field, or changing a field's type or meaning.
- Adding a new field is not breaking. Consumers should ignore fields they don't know.
- The same inputs, policy and EvalGate version always produce byte-identical output. No timestamps are included, and lists are sorted by stable keys.
- Raw prompts and model outputs are never included.

## Top level

| Field | Type | Meaning |
|---|---|---|
| `report_version` | `"1"` | Version of this format |
| `evalgate_version` | string | EvalGate version that produced the report |
| `status` | `"PASS"` \| `"FAIL"` \| `"INVALID"` | The decision. CLI exit codes are 0 / 1 / 2 |
| `baseline`, `candidate` | manifest \| null | Contents of each run's `run.json`, including extra metadata fields. null if the run could not be loaded |
| `policy` | policy \| null | The policy that was applied, with defaults filled in. null if it could not be loaded |
| `diagnostics` | list | Validation findings (below) |
| `comparison` | object \| null | Measured differences (below). null when the result is INVALID before comparing |
| `rules` | list | One result per policy rule (below). Empty when the result is INVALID before evaluating |

**Decision rule:** with `statistics.require_conclusive: true`, a metric rule passes only if it is also `conclusive`. Then: any diagnostic with `level: "error"` makes the status `INVALID`. Otherwise, any rule with `passed: false` makes it `FAIL`. Otherwise the status is `PASS`.

## `diagnostics[]`

| Field | Type | Meaning |
|---|---|---|
| `level` | `"error"` \| `"warning"` | `error` means INVALID; `warning` is informational |
| `code` | string | Stable machine-readable code |
| `message` | string | Human explanation. Do not parse it |

Codes:
- **Loading:** `run_dir_missing`, `file_missing`, `file_unreadable`, `schema`, `no_cases`, `policy_yaml`
- **Validation:** `duplicate_case_id`, `dataset_mismatch`, `evaluator_mismatch`, `case_mismatch`, `no_comparable_cases`, `too_few_cases`, `small_sample` (warning), `metric_missing`, `check_missing`
- **Evaluation:** `pct_undefined`, `numeric_overflow`

## `comparison`

| Field | Type | Meaning |
|---|---|---|
| `matched_cases` | int | Case IDs present in both runs |
| `compared_cases` | int | Matched cases with `status: "ok"` in both runs. All means are computed over these |
| `baseline_errors` | list[str] | Matched case IDs with `status: "error"` in the baseline |
| `candidate_errors` | list[str] | Candidate case IDs with `status: "error"`, including unmatched ones |
| `metrics[]` | list | Per policy metric: `name`, `path`, `direction`, `cases`, `baseline_mean`, `candidate_mean`, `delta_abs` (candidate minus baseline), `delta_pct` (null when the baseline mean is 0), `ci` (below) |
| `regressed_cases[]`, `improved_cases[]` | list | Per case and metric, changes in the worse or better direction: `case_id`, `metric`, `baseline`, `candidate`, `delta_abs`. Sorted by metric, then largest change first |
| `newly_failing_checks[]`, `newly_passing_checks[]` | list | `{case_id, check}` pairs that flipped between runs (compared cases only) |
| `candidate_failing_checks[]` | list | Every `{case_id, check}` that is false in an `ok` candidate case |
| `check_stats[]` | list | Per check present in both runs of a compared case: `check`, `cases`, `baseline_failures`, `candidate_failures`, `newly_failing`, `newly_passing`, `p_value` (below). Sorted by check |
| `categories[]` | list | Per `metadata.category` (taken from the baseline) and metric: `category`, `metric`, `cases`, `baseline_mean`, `candidate_mean`, `delta_abs` |

`delta_abs` is always candidate minus baseline. Whether that is better or worse depends on `direction`.

### `metrics[].ci`

Confidence interval for the paired mean change `delta_abs`.

| Field | Type | Meaning |
|---|---|---|
| `confidence` | float | Interval level, 0.5–0.999 (`policy.statistics.confidence`, default 0.95) |
| `low`, `high` | float | Bounds for `delta_abs` |
| `pct_low`, `pct_high` | float \| null | Bounds as a percent of the baseline mean, which is treated as fixed. null when that mean is 0 |
| `method` | `"bootstrap"` \| `"normal"` | Percentile bootstrap over paired cases for up to 2000 cases. Above that, the normal approximation (CLT), which is what the bootstrap converges to |
| `resamples` | int | Bootstrap resamples (1000), or 0 for `normal` |

The bootstrap uses a fixed seed, so the interval is deterministic. **Assumptions:** cases are independent, and a candidate case is paired with the baseline case of the same `case_id`. On small, skewed samples the percentile bootstrap can be too narrow: in a check with 40 cases from an exponential distribution, it covered about 90% instead of the nominal 95%.

### `check_stats[].p_value`

One-sided exact McNemar test of whether the candidate fails the check more often than the baseline. It uses only discordant pairs: if nothing changed, each flip is equally likely to go either way, so `p = P(X >= newly_failing)` for `X ~ Binomial(newly_failing + newly_passing, 1/2)`. The value is computed in log space and matches exact integer arithmetic to a relative error below 1e-10. A p of 1 when nothing newly failed is correct. With few flips the test can't reach significance (3 of 3 newly failing gives p = 0.125), so a large p means "no evidence", not "no regression". Each check is tested separately, so with many checks a chance FAIL somewhere is more likely; that errs toward failing, never toward passing.

## `rules[]`

| Field | Type | Meaning |
|---|---|---|
| `rule` | string | Rule identifier (below) |
| `passed` | bool | Whether the candidate satisfies it |
| `message` | string | Human explanation with the measured values. Do not parse it |
| `conclusive` | bool \| null | Metric rules only: true when the whole CI of the regression lies on one side of the threshold, over at least 2 cases. null for other rules |

Rule identifiers: `max_execution_errors`, `metrics.<name>.max_mean_regression_abs`, `metrics.<name>.max_mean_regression_pct`, `checks.required.<check>`, `checks.max_new_failures`, `checks.significant_regression.<check>` (only with `checks.fail_on_significant_regression: true`, which must be combined with another rule; fails when `p_value < 1 - statistics.confidence`).
