# Statistics: methods, assumptions and limits

EvalGate's statistics are there so a release decision can tell a real change from noise. They never make a decision more lenient. A threshold rule fails when the observed change breaks its limit, whatever the interval says. The statistics options can only add failures.

Everything here is deterministic: the same inputs give the same numbers on every run and every machine.

## The unit of analysis is the case

Every statistic pairs a case's baseline result with the same case's candidate result. Only cases with `status: ok` in both runs are compared. Cases that errored in either run are left out of the means and intervals, and they're counted against `max_execution_errors` separately.

With repeated runs, each case's repeats are first collapsed into one result (see [repeated runs](design/repeated-runs.md)): metric values are averaged, a check passes only if every repeat passes, and any errored repeat makes the whole case an error. So 50 cases × 5 repeats is still a sample of 50, not 250. Repeats make each case's value less noisy. They don't make it look as if you have more cases.

## Mean change and its confidence interval

For each metric, `delta_abs` is the mean over compared cases of candidate minus baseline. `delta_pct` divides that by the absolute baseline mean. It's undefined when the baseline mean is 0, so EvalGate never divides by zero: a percent limit on such a metric is INVALID unless an absolute limit is also set, in which case the absolute limit is used and a warning is shown.

The interval in `ci` is for the paired mean difference, at `statistics.confidence` (default 0.95):

| Compared cases | Method | Details |
|---|---|---|
| 1 | none | Zero-width interval at the observed change. It proves nothing and is never *conclusive*. |
| 2 to 2000 | Percentile bootstrap | 1000 resamples of the per-case differences with a fixed seed. The bounds are the α/2 and 1−α/2 percentiles of the resampled means. |
| over 2000 | Normal approximation | mean ± z × sd / √n. At this size the mean's sampling distribution is close to normal, and resampling in pure Python gets slow. |

Percent bounds (`pct_low`, `pct_high`) divide the absolute bounds by the observed baseline mean, treating it as fixed. That understates the uncertainty a little when the baseline mean itself is noisy.

### Conclusive and inconclusive

A threshold rule is *conclusive* when the whole interval lies on one side of its limit. Either the worst plausible change is still within the limit, or even the best plausible change breaks it. Otherwise the rule message ends in `inconclusive`.

By default an inconclusive rule is decided by the observed change, and the report just says it's inconclusive. With `statistics.require_conclusive: true`, a rule passes only if it's within its limit **and** conclusive. A rule that's within its limit but inconclusive then fails. Use this when "we can't tell" should block a release.

## Check flips: exact McNemar test

For a pass/fail check, only the cases that changed carry information: `newly_failing` (passed in baseline, fails in candidate) and `newly_passing` (the reverse). `p_value` in `check_stats` is a one-sided exact McNemar test of "the candidate fails this check more often". If nothing really changed, each flip is equally likely to go either way, so `newly_failing` follows Binomial(newly_failing + newly_passing, ½), and p is the chance of at least the observed count.

With `checks.fail_on_significant_regression: true`, a check fails when p < 1 − `statistics.confidence`. With few flips this test can't reach significance: 4 newly failing and 0 newly passing gives p = 0.0625. That's why a policy can't rely on this flag alone. Pair it with `max_new_failures` or `required`.

## Category limits

`max_category_regression_abs` and `max_category_regression_pct` on a metric apply the same kind of limit to each `metadata.category` separately, with its own mean change and interval. The category comes from the baseline case, or from the first repeat with repeated runs. Only text values count.

- A category limit with no categorized compared cases is INVALID (`category_missing`), not a silent pass.
- Compared cases without a category aren't covered by category limits. A `category_partial` warning says how many.
- Categories are smaller than the whole suite, so their intervals are wider. With `require_conclusive`, a small category often comes out inconclusive and fails. A single-case category always does.
- Each category is another chance for noise to break a limit: with many categories, some will look worse by chance. Set category limits looser than the overall limit, or use `require_conclusive` so only clear regressions fail.

## Sample size

Below 30 compared cases EvalGate adds a `small_sample` warning: mean changes on so few cases are often noise. `comparison.min_compared_cases` turns that into a hard floor: fewer compared cases is INVALID (`too_few_cases`).

## What the statistics don't do

- **No correction for multiple comparisons.** Every metric, category and check is tested on its own. The more rules a policy has, the more likely one fails by chance at a given confidence level.
- **Cases are assumed independent.** If cases are near-duplicates, or several cases come from one conversation, the intervals are too narrow.
- **The bootstrap needs enough cases to be reliable.** With fewer than about 20 cases the percentile interval tends to be too narrow, and a narrow interval can make a rule look conclusive when it isn't. Use more cases or `min_compared_cases`.
- **Only the mean is tested.** A change that keeps the mean but widens the spread, such as a few much worse cases balanced by many slightly better ones, doesn't show up in a metric rule. Look at `regressed_cases` and the per-category table.
- **Evaluator noise is not modeled.** A nondeterministic judge adds variance between runs. Repeats reduce it per case, but the intervals don't separate judge noise from model change.
- **The baseline is fixed data.** EvalGate compares two runs. It doesn't say anything about runs it hasn't seen.
