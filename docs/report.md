# The HTML report

`evalgate compare … --report report.html` (or `Report.to_html(path)`) writes one standalone HTML file. You can open it from disk, attach it to an email or keep it as a CI artifact. It loads nothing from the network: no CDN, no web fonts, no images. [The JSON report](report-format.md) is the machine-readable artifact; the HTML is a view of it and never shows data that isn't in the JSON. No report contains raw prompts or model outputs.

## What it shows

- **The decision** (`decision: FAIL`) and its exit code.
- **Failed policies and diagnostics.**
- **Runs:** both manifests, side by side.
- **Metrics:** means, change, percentage change and confidence interval.
- **Policy rules:** every rule, with pass or fail.
- **Execution errors:** the case ids that errored in the baseline and in the candidate.
- **Case changes:**
  - newly failing and newly passing checks;
  - regressed and improved cases;
  - the candidate's failing checks.
- **Failure groups:** the candidate's failing checks on compared cases, by check × category, by check and by category. Each failure counts as `new`, `persistent` or `no baseline`, as in the [report format](report-format.md#comparisonfailure_groups). This section is not shown for an INVALID report, or when nothing fails.
- **Repeats, checks (with McNemar p-values) and categories,** when the report has them.
- **The policy** that was applied.

## Using it

- **Sort:** click (or tab to and press Enter on) a column header. A second click reverses the order.
  - Numbers sort by their full value, not the 6 digits shown.
  - Empty and `n/a` cells always go last.
  - Text sorts by character code, so the order is the same in every browser and locale.
- **Filter:** case-level tables (case lists, errors, failure groups, categories) get a filter box that hides the rows not containing what you type, and shows "n of m rows".
- **Fold:** every section is a collapsible block, open by default so find-in-page and printing see everything.

Without JavaScript (a strict email client, a viewer that strips scripts) the report is complete and correct: every row is in the page. Only sorting and filtering need the script.

## Security

The page carries a Content Security Policy:

```
default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-…'; img-src data:; base-uri 'none'; form-action 'none'
```

**What it allows:**
- Exactly one script runs: the viewer that EvalGate embeds, pinned by its hash.
- Nothing can be fetched, framed or submitted.
- No inline event handler can run.

**Escaping:** every value from your runs (case ids, check names, labels, run ids) is HTML-escaped. If escaping ever had a bug, the policy would still stop injected markup from running script or loading anything.

**How the viewer works:** it moves and hides rows that are already in the page. It makes no requests and never builds markup from strings.

## The history page

`evalgate history report OUT.html --history DIR` writes a similar standalone page for a [history store](history.md#the-history-page): one trend chart per metric, the entries, and the checks that keep failing. It uses the same look, the same script and the same policy.
