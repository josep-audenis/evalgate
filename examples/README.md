# Examples

Each directory holds a baseline run, a candidate run and a policy. Run one with:

```bash
evalgate compare examples/<name>/baseline examples/<name>/candidate \
  --policy examples/<name>/evalgate.yaml --report report.html
```

| Example | Scenario | Decision |
|---|---|---|
| [quickstart](quickstart) | Mean quality improves, but one case newly fails the `safety_valid` check | **FAIL**: required check and new-failure rules |
| [cost-latency](cost-latency) | A bigger model raises quality by +0.105 but costs +304% and is 57% slower | **FAIL**: cost and latency percent rules |
| [repeats](repeats) | Each case evaluated 3 times per version; average quality is unchanged, but the candidate is ~5x noisier and its safety check now fails in 1 of 3 repeats on two cases | **FAIL**: required check and new-failure rules (strict: every repeat must pass) |
| [import-csv](import-csv) | Two CSV exports from another tool, imported with one mapping (`evalgate import ... --run-id`); one candidate case timed out and one lost its citation | **FAIL**: `max_execution_errors` and required check |
| [sdk_quickstart.py](sdk_quickstart.py) | Records both runs from a Python eval loop with `EvalRun`; one candidate case times out | **FAIL**: `max_execution_errors` |

In every example the average looks the same or better, but the release still fails. Averages can hide a critical regression or a cost/latency budget overrun.
