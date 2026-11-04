# CI integration

`evalgate compare` exits with:

| Code | Decision | Meaning |
|---|---|---|
| `0` | PASS | Valid comparison; every policy rule holds |
| `1` | FAIL | Valid comparison; at least one release rule is violated |
| `2` | INVALID | Missing or malformed input, incomparable runs, or a tool error |

Any non-zero exit fails a CI step, so no extra scripting is needed. To tell a policy failure apart from broken input, read `status` from the JSON report.

## GitHub Actions

This assumes your eval job has already written the candidate run to `evals/candidate/`, and that the approved baseline is committed at `evals/baseline/`.

```yaml
name: eval-gate

on: pull_request

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install evalgate

      # ... your step that runs the evals and writes evals/candidate/ ...

      - name: Release gate
        run: |
          evalgate compare evals/baseline evals/candidate \
            --policy evalgate.yaml --json report.json --report report.html

      - name: Upload report
        if: always()  # keep the report when the gate fails
        uses: actions/upload-artifact@v4
        with:
          name: evalgate-report
          path: |
            report.json
            report.html
```

## Updating the baseline

EvalGate never promotes a candidate to baseline on its own. When you approve a new version, replace `evals/baseline/` with its run in a normal, reviewed commit. The diff of `run.json` then records which version became the baseline, and when.

## Any other CI system

Run the same command and rely on the exit code. Keep `report.json` and `report.html` as build artifacts so the reasons for the decision stay inspectable.
