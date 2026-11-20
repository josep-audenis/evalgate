# Several candidates

When you have more than one release candidate (two prompt variants, three models), judge them all against the same baseline in one command:

```bash
evalgate compare evals/baseline evals/cand-a evals/cand-b evals/cand-c \
  --policy evalgate.yaml --rank-by quality \
  --json reports/cand-{n}.json --summary reports/summary.json
```

Each candidate is compared with the baseline exactly as `evalgate compare BASELINE CANDIDATE` would compare it: same engine, same policy, same report. Candidates are never compared with each other, and none of them becomes a baseline. With one candidate, `compare` behaves exactly as before. [examples/multi-candidate](../examples/multi-candidate) is a runnable version.

From Python: `evalgate.compare_many(baseline, [cand_a, cand_b], policy, rank_by="quality")` returns the reports, the exit code, the ranking and `summary()`. `candidates` must be a list or tuple: a single path, or an unordered set, raises `TypeError`. An empty list, or a `rank_by` that isn't a metric of a policy that loads, raises `ValueError`.

## Exit code

| Code | When |
|---|---|
| 2 | any candidate is INVALID |
| 1 | otherwise, any candidate FAILs |
| 0 | every candidate PASSes |

One exit code can only safely answer "did every candidate pass?". To choose among them, use the per-candidate statuses, the ranking and the summary.

## Artifacts

- With more than one candidate, the `--json`, `--report` and `--markdown` paths must contain `{n}`, the candidate's position on the command line (1, 2, …). Otherwise the command stops before comparing anything (exit 2), so reports never overwrite each other.
  - Every `{n}` is replaced; other braces stay as they are. With a single candidate, a `{n}` in a path stays literal.
  - Parent directories must already exist.
  - Each file is the same report a two-run compare would write.
- `--summary PATH` writes one JSON file for any number of candidates:
  ```json
  {"summary_version": 1, "evalgate_version": "0.12.0", "baseline_run_id": "baseline",
   "exit_code": 1, "rank_by": "quality",
   "candidates": [{"n": 1, "run_id": "candidate-a", "status": "FAIL"}, …],
   "ranking": [{"rank": 1, "n": 1, "run_id": "candidate-a", "status": "FAIL", "value": 0.88, "cases": 6}, …],
   "cases_differ": false}
  ```
  - `run_id` and `baseline_run_id` are `null` when that comparison's inputs (either run, or the policy) didn't load.
  - `ranking` is `null` without `--rank-by`, and `[]` when no candidate could be ranked.
  - The file has no timestamps, so the same inputs give the same bytes.

## Ranking (`--rank-by`)

`--rank-by NAME` sorts candidates by one policy metric's candidate mean, in the metric's declared direction. It is informational only:
- **No aggregate score.** One metric, no weights.
- **Never a decision.** The ranking changes no status, no exit code and no report. A FAIL candidate is ranked like any other and shown as FAIL: the ranking says which candidate is best on that metric, not which may ship. In the example, the best-quality candidate FAILs on safety.
- **Excluded:** INVALID candidates.
- **Ties** keep command-line order. `rank` is the position (1, 2, 3…), with no shared ranks.
- **Unknown names:** a name that isn't a policy metric exits 2 before comparing (the name isn't checked when the policy itself can't be loaded).
- **Different case counts:** each item carries `cases`, the number of compared cases behind its mean. When those differ, for example because one candidate errored on some cases, a warning is printed and `cases_differ` is `true`. Means over different cases aren't directly comparable.

## Order and failures

The order is:
1. decide every candidate;
2. write each candidate's artifacts, in order;
3. write the summary;
4. record history;
5. print everything.

**If a write fails:**
- writing stops at that point;
- every decision is still printed, then the error;
- the command exits 2;
- the summary's `exit_code` is the decision's, so it can say 1 while the process exits 2.

**With `--history`:**
- one compare entry is recorded per candidate, in order, all with the same `--label`, which is what links them;
- if an append fails partway, the earlier entries stay recorded and the output says how many.

## Paths

A candidate path that starts with `-` needs `--` before it, for example `evalgate compare base -- -weird-dir`.
