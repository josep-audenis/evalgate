# Importing results from other tools

`evalgate import` converts a results file from any tool into an EvalGate run directory, using a small mapping file. No tool-specific code is needed.

```bash
evalgate import results.csv --mapping mapping.yaml --out evals/candidate --run-id candidate-v2
```

```python
from evalgate import import_results

import_results("results.csv", "mapping.yaml", "evals/candidate", run_id="candidate-v2")
```

Runnable example: [examples/import-csv](../examples/import-csv). It imports two CSV exports with one mapping and compares them.

For promptfoo, use the built-in adapter instead: [importing promptfoo results](promptfoo.md).

## Supported input

| Format | Shape | Chosen by |
|---|---|---|
| CSV | header row + one row per result | `.csv` extension |
| JSON | a top-level list of objects | `.json` extension |
| JSONL | one object per line | `.jsonl` extension |

If the extension doesn't match, set `format: csv | json | jsonl` in the mapping. For JSON and JSONL, a field can be a dotted path into nested objects (`output.scores.quality`). Paths go through objects only, not lists. An exact key match always wins, so CSV headers that contain dots still work.

Duplicate CSV column names and duplicate JSON keys are rejected, since otherwise the last one would silently win. Every CSV row must have exactly as many fields as the header, so an unquoted `1,200` can't shift the columns.

## Mapping file

```yaml
version: 1
format: csv                 # optional; inferred from the extension

manifest:                   # becomes run.json
  run_id: candidate-v2      # optional here if you pass --run-id
  dataset_id: faq-suite
  dataset_version: v2
  evaluator_version: judge-v1
  application: faq-bot      # optional
  application_version: "1.4"  # optional (quote version-like values)

case_id: id                 # required: field holding the stable case ID
repeat: attempt             # optional: 0-based repeat index (see repeated runs)
status: outcome             # optional: without it every record counts as "ok"
status_values:              # optional: translate the tool's words to ok / error
  success: ok
  timeout: error
error: error_message        # optional: error text for "error" records

scores:   {quality: judge_score}      # EvalGate name: source field
metrics:  {latency_ms: latency}
checks:   {cites_source: has_citation}
metadata: {category: topic}
```

- At least one of `scores`, `metrics` or `checks` is required.
- Unknown or duplicate keys are rejected, so a typo can't silently drop a column.

## Conversion rules (fail closed)

- **Numbers** (`scores`, `metrics`) must be JSON numbers or plain ASCII decimal text such as `0.9`, `-3` or `1e-3`. `true`, `"abc"`, `nan`, `inf`, `1_000`, `1,200` and non-ASCII digits are errors. Export numbers **without thousands separators**. An unquoted `1,200` is caught only when it changes the field count. If another column is empty at the same time, the count still matches and the values shift unnoticed, so no CSV parser can detect it.
- **Checks** accept `true/false`, `yes/no`, `pass/fail`, `passed/failed` and `1/0`, in any case. Anything else is an error, never a guess.
- **Empty cells and missing fields** leave that value out. If your policy needs it, `compare` then reports `metric_missing` or `check_missing` (INVALID). Nothing is filled in.
- **Status** values must be `ok`/`error` or be mapped in `status_values`. An unknown value is an error. For `error` records, scores are ignored and the case counts against `max_execution_errors`.
- **`case_id`** must be text or an integer. Booleans, objects and lists are errors.
- **A mapped field that appears in no record at all** is an error (`import_field_missing`), because it's almost always a typo in the mapping.
- **Every** bad record is reported with its record number (1-based). If any record fails, **no run files are written** (`run.json`, `cases.jsonl`).
- Duplicate case IDs (or duplicate case ID + repeat pairs) are errors.
- `--overwrite` is needed to replace an existing run. An existing run stays intact if the import fails.

## Privacy

Only mapped fields are imported. Unmapped columns, typically prompts and model outputs, are never copied into the run. The command lists unmapped top-level fields as `not imported (unmapped)` so you can check. A mapped `metadata` field holding an object is copied whole, so don't map an object that contains prompts.

## Repeated runs

Map `repeat` to a 0-based index field. The importer sets `schema_version: "1.1"` and `repeats` to the largest index + 1. A case missing one of the repeats makes `compare` INVALID (`repeat_incomplete`).
