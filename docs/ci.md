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
      - run: pip install evalgate-ai

      # ... your step that runs the evals and writes evals/candidate/ ...

      - name: Release gate
        run: |
          evalgate compare evals/baseline evals/candidate \
            --policy evalgate.yaml --json report.json --report report.html --markdown report.md

      - name: Job summary
        if: always()  # show the decision when the gate fails too
        run: '[ -f report.md ] && cat report.md >> "$GITHUB_STEP_SUMMARY"'

      - name: Upload report
        if: always()  # keep the report when the gate fails
        uses: actions/upload-artifact@v4
        with:
          name: evalgate-report
          path: |
            report.json
            report.html
            report.md
```

`--markdown` writes a short summary: the decision, failed rules first, metrics with their confidence intervals, and collapsed lists of regressed cases and newly failing checks. Text from your runs (case ids, run ids, metric names, rule messages) is shown as inline code, which GitHub renders literally: it can't break the tables, add HTML, links or math, or @mention people and reference issues. Long values are cut at 300 characters and long lists at 10 items, so the summary stays under GitHub's 65,536-character comment limit; the JSON and HTML reports have everything.

To also post it on the pull request, add a step and give the job write access to pull requests. Listing `permissions` drops every scope you don't list, so keep `contents: read` for the checkout:

```yaml
jobs:
  gate:
    permissions:
      contents: read
      pull-requests: write
    steps:
      # ... the steps above ...
      - name: Comment on the PR
        if: always()
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh pr comment ${{ github.event.pull_request.number }} --body-file report.md
```

This adds a new comment on every run. To keep one comment updated instead, use `gh pr comment --edit-last --create-if-none`. That edits the last comment by the same account, which for `GITHUB_TOKEN` is `github-actions[bot]`, so it can collide with comments from other workflows. On pull requests from forks, `GITHUB_TOKEN` is read-only and the comment step fails; the job summary still works.

## Updating the baseline

EvalGate never promotes a candidate to baseline on its own. When you approve a new version, replace `evals/baseline/` with its run in a normal, reviewed commit. The diff of `run.json` then records which version became the baseline, and when.

## Any other CI system

Run the same command and rely on the exit code. Keep `report.json` and `report.html` as build artifacts, and paste `report.md` wherever your system shows Markdown (merge request comments, for example), so the reasons for the decision stay inspectable.
