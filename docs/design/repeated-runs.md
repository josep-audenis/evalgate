# Design: repeated runs

Status: **implemented** (approved 2026-11-04).

## Problem

LLM applications are nondeterministic. Running the same version twice on the same cases gives different scores, so a single run per version mixes the real change with run-to-run noise. Teams handle this by evaluating each case several times. EvalGate currently rejects that data: a repeated `case_id` is a `duplicate_case_id` error.

SPEC §9.2 sets the constraint: *repeated model generations for one case should not automatically be treated as independent test cases.* Treating 3 repeats × 100 cases as 300 cases would make every confidence interval about √3 times too narrow, so noise would look like signal.

## Principle

**The case stays the unit of analysis.** The repeats of a case are first combined into one value for that case. Everything downstream (means, deltas, confidence intervals, rules, sample-size warnings) then works on cases exactly as it does today.

As a result, a run with one repeat per case behaves exactly like 0.2.0. Repeats make each case's value more precise, but they never increase the sample size.

## 1. Data format

One run directory still holds one version. Repeats are extra lines in `cases.jsonl`, with a new optional integer field `repeat`:

```json
{"case_id": "refund-001", "repeat": 0, "status": "ok", "scores": {"quality": 0.82}, "checks": {"safe": true}}
{"case_id": "refund-001", "repeat": 1, "status": "ok", "scores": {"quality": 0.90}, "checks": {"safe": true}}
{"case_id": "refund-001", "repeat": 2, "status": "ok", "scores": {"quality": 0.79}, "checks": {"safe": false}}
```

- `run.json` gains an optional `repeats: k`, which declares the expected number of repeats per case.
- `schema_version` becomes `"1.1"`. Version `"1.0"` files keep working unchanged. Older EvalGate versions will reject a `"1.1"` file with a clear schema error, rather than misreading the repeats as duplicates.

I chose this over several run directories per version (for example `compare base1 base2 --vs cand1 cand2`). It keeps the CLI, the SDK and the "one directory per version" model unchanged, and a single file can't be half-uploaded across directories.

## 2. Validation (fail closed)

The result is INVALID when:
- a (`case_id`, `repeat`) pair appears twice
- some lines of a run have `repeat` and others don't
- repeat numbers are not exactly `0 … k-1` for every case, using the declared `repeats` if present, otherwise the largest observed count

A missing repeat counts as a missing evaluation and is never silently averaged around.

The baseline and candidate may use different `k` (say 3 and 5). Each case is still paired by `case_id`. The report shows both `k` values.

## 3. Combining repeats into one case value

| Field | Case value |
|---|---|
| metric (`scores.*`, `metrics.*`) | mean over the case's repeats |
| check | passes only if it passes in **every** repeat (strict) |
| check pass rate (new, report only) | fraction of the case's repeats that pass |
| `status` | `error` if **any** repeat errored |
| `metadata` | taken from repeat 0 |

The combined cases then go through the existing pipeline unchanged.

## 4. What changes downstream

Almost nothing:
- **Means, deltas, `regressed_cases`/`improved_cases`, categories:** unchanged, computed over combined case values.
- **Confidence intervals:** unchanged code. The bootstrap resamples **cases**, each carrying its combined value (a cluster bootstrap), so repeat-to-repeat noise is already inside each case's value and isn't double-counted. This is the property the spec asks for.
- **`required` checks, `max_new_failures`, McNemar:** use the strict per-case outcome. A check that is flaky only in the candidate is therefore *newly failing*, because the candidate became less reliable.
- **`max_execution_errors`:** counts cases with at least one errored repeat.
- **`small_sample` / `min_compared_cases`:** count cases, not repeats.

New, report-only additions:
- `baseline.repeats` / `candidate.repeats` in the comparison.
- **Per-check pass rate:** the average over cases of each case's pass fraction, for both runs, so flakiness is visible even when the strict outcome is the same.
- **Per-metric noise level:** the average spread (SD) of the repeats within a case, for both runs. This shows how much of a delta could be run-to-run noise. It is shown only when k ≥ 2.

## 5. SDK

`EvalRun(..., repeats=3)`, then `run.record(case_id, repeat=i, ...)` and `run.record_error(case_id, error, repeat=i)`. Declaring `repeats` writes it to `run.json`, so a run that recorded fewer repeats fails validation at compare time. No new crash-safety logic is needed: the existing commit-on-clean-exit still holds.

## Decisions

Approved on 2026-11-04:

1. **Checks are combined strictly (§3).** A check passes for a case only if it passes in every repeat. Pass rates are reported so flakiness stays visible. (Rejected: majority vote, which can hide a check that now fails 1 time in 3.)
2. **An errored repeat makes the whole case an error (§3).** (Rejected: dropping errored repeats, which biases toward easy inputs.)
3. **Schema version `"1.1"` for runs with repeats (§1).** Older versions reject such files outright, and `"1.0"` files keep working.

## Out of scope

- Variance-components or mixed models, and ICC.
- Pairing repeat *i* of the baseline with repeat *i* of the candidate. Repeat numbers have no meaning across runs, so this would be wrong.
- Choosing `k` for the user, or power analysis.
- Per-repeat rows in the HTML case tables (case-level only).
