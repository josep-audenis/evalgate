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
| [import-csv](import-csv) | Two CSV exports from another tool, imported with one mapping (`evalgate import ... --run-id`); one candidate case timed out and one lost its citation | **FAIL**: `max_execution_errors` and required check |
| [sdk_quickstart.py](sdk_quickstart.py) | Records both runs from a Python eval loop with `EvalRun`; one candidate case times out | **FAIL**: `max_execution_errors` |

In both directory examples the average looks better, but the release still fails. Averages can hide a critical regression or a cost/latency budget overrun.
