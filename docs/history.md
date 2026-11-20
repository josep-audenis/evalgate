# Local history

`evalgate compare` answers one question about one pair of runs. A local history store keeps a record across releases, so you can see how a metric moved and which entries failed. It is a plain directory you choose. Nothing is recorded unless you ask, and nothing leaves your machine.

```bash
# record a release decision (the exit code is still the decision's: 0 / 1 / 2)
evalgate compare evals/v1.4.0 evals/v1.5.0 --policy evalgate.yaml \
  --history .evalgate/history --label v1.5.0

# record a run on its own (means, pass rates, failing checks)
evalgate history add evals/v1.5.0 --history .evalgate/history --label v1.5.0

evalgate history list --history .evalgate/history
evalgate history show 3 --history .evalgate/history
evalgate history trend scores.quality --history .evalgate/history --dataset regression-suite@v3
```

From Python: `evalgate.record_report(report, dir, label=...)`, `evalgate.record_run(run_dir, dir, label=...)`, `evalgate.read_history(dir)` and `evalgate.load_entry_report(dir, id)`. The CLI is a thin wrapper over these. [examples/history](../examples/history) builds a small store.

## What's stored

```
.evalgate/history/
  index.jsonl          one entry per line, in recording order
  reports/000003.json  the full report of compare entry 3, byte-identical to --json
```

**Entries come in two kinds.**
- **`compare`** (from `compare --history`):
  - the status (PASS / FAIL / INVALID);
  - the baseline and candidate identities: `run_id`, `application`, `application_version` and dataset;
  - for each policy metric path, the baseline mean, candidate mean, change and case count;
  - the candidate's failing `(case_id, check)` pairs among compared cases (cases `ok` in both runs);
  - the SHA-256 of the stored report.

  An INVALID compare is recorded too, since "this pair couldn't be compared" is worth keeping. Its identities are empty when a run didn't load, and it has no metrics.
- **`run`** (from `history add`):
  - the run identity, and the number of cases and errored cases (repeats collapsed);
  - every score and metric mean, and every check pass rate, each over the ok cases that have it, with that count;
  - the failing `(case_id, check)` pairs;
  - the ids of its ok cases (`case_ids`), so `history failures` can tell "failed in 2 of 3 runs that had this case" from "failed twice".

  An INVALID run isn't recorded: it has no data point to give.

**What a store holds and doesn't hold.**
- **Never** model outputs, prompts or `case` data. Reports never carry them, and run summaries read only scores, metrics and checks.
- **It holds case ids:** every run entry lists the ids of all its ok cases. If your case ids embed user data, so does the store. A larger index also makes each append, which rewrites the index, a little slower.
- **It can hold** whatever `--json` holds:
  - for an INVALID report, file paths and parser messages in its diagnostics;
  - in stored reports, any extra fields you put in `run.json`.

  Treat a store like the reports it contains.

**Ids and order.**
- Each entry has an `id`: 1, 2, 3… in recording order. Every listing and trend uses that order, never wall-clock time.
- `recorded_at` (UTC) is only shown.
- An optional `--label` (up to 200 characters) names an entry. Markup in it is never interpreted, and control characters are shown escaped.

**Versions.**
- Since 0.12.0, entries are written as `history_version: 2`; earlier entries are version 1, and EvalGate reads both, so a store may mix them.
- What version 2 adds:
  - run entries carry `case_ids`;
  - compare entries take their failing pairs strictly from compared cases. A version 1 compare entry recorded with `require_same_cases: false` may also list cases only the candidate had.
- EvalGate 0.10 and 0.11 report a version 2 line as an unsupported `history_version`: upgrade every tool that writes to a shared store together.
- In `list --json` and `show --json`, version 1 run entries show `"case_ids": null`.

**Editing.** The store is append-only: EvalGate never edits or deletes entries.
- **To prune,** delete lines from `index.jsonl` yourself. Ids may have gaps, and the next entry gets the last id + 1. Pruning the last line therefore reuses its id, and the next compare overwrites its old `reports/<id>.json`.
- **What's rejected:** a line EvalGate can't read (a duplicate or decreasing id, a typo, an unknown field). It is reported by line number, without its content, and EvalGate won't append to a store until it's fixed.

## Reading

| Command | Shows |
|---|---|
| `history list [--last N] [--json]` | One row per entry: id, kind, label, status (`valid` for run entries), candidate or run, dataset, time |
| `history show ID [--json]` | A compare entry: its stored report, rendered like `compare` (after checking its hash). A run entry: its means and pass rates |
| `history trend PATH [--kind run\|compare] [--dataset ID[@VERSION]] [--application APP] [--last N] [--json]` | One metric path per entry, in id order |
| `history failures [--kind run\|compare] [--dataset …] [--application …] [--last N] [--min K] [--json]` | `(case, check)` pairs that keep failing across entries (below) |

About `trend`:
- **Kinds are never mixed** (`--kind` defaults to `run`). A compare's mean covers only the cases both runs completed, while a run's mean covers all of its ok cases, so the two aren't the same number.
- **Datasets:** filter with `--dataset`, which matches the dataset id exactly, or the exact `id@version` (if an id itself contains `@`, both readings are tried, each exactly). A trend whose points span more than one dataset prints a warning (`mixed_datasets` in JSON), because means over different datasets aren't comparable. The check uses the points left after `--last`.
- **Filters:** `--application` matches the run's application, or the candidate's application for compares. `--last N` applies after filtering.
- **Skips:** entries without the path are skipped and counted.

About `failures`:
- **Selection:** entries are chosen as for `trend`, except that INVALID compares are left out, since they carry no data.
- **For each `(case_id, check)` pair that fails in at least `--min` entries** (default 2), it reports:
  - `failing`: how many selected entries it fails in;
  - `of`: how many selected entries contain the case. It is shown for run entries only, and `n/a` when any selected run entry is version 1 (no `case_ids`) or for `--kind compare`;
  - `first` and `last`: the ids of the first and last entries it fails in;
  - `streak`: how many of the most recent selected entries it fails in, without a gap.
- **What ends a streak:** any entry where the pair doesn't fail, including one where the case errored or is missing. So a broken streak isn't proof the failure is fixed.
- **Order:** the longest current streak first, then by `failing`.
- **Multi-candidate stores:** entries from one `compare` run with several candidates count separately. Narrow with `--application` when the candidates differ.

**JSON output:**
- `list --json` gives `{"history_version": 1, "entries": [...]}`.
- `trend --json` gives `{"history_version": 1, "path", "kind", "points": [{id, label, recorded_at, application_version, dataset, value, cases}], "skipped", "mixed_datasets"}`. An INVALID compare whose candidate didn't load counts as skipped when no filter is given.
- `failures --json` gives `{"history_version": 1, "kind", "entries", "pairs": [{case_id, check, failing, of, first, last, streak}], "mixed_datasets"}`.
- JSON output escapes non-ASCII characters, so it survives any console encoding when piped.
- `show --json` gives `{"entry": …}` for a run entry and `{"entry": …, "report": …}` for a compare entry.

## Exit codes and failures

**History commands:**
- They exit 0, or 2 on any problem; they never exit 1.
- In Python, a `recorded_at` without a timezone raises `ValueError`.
- Diagnostics: `history_missing` (no store), `history_parse` (a bad line, or an index that can't be read), `history_id` (an unknown id, or a stored report that is missing or doesn't match its entry), `history_record` (an INVALID or unsummarisable run, or a label that's too long) and `history_write` (an OS error).
- `compare` rejects a bad `--label` (too long, or given without `--history`) up front, with exit 2, before comparing.
- A run directory that doesn't load reports the usual codes (`run_dir_missing`, `file_missing`, …).

**`compare --history`:**
- It writes `--json` / `--report` / `--markdown` first, then the history entry, then prints the decision. The exit code is the decision's.
- If the history entry can't be written, the decision is still printed, followed by the history diagnostics, and the command **exits 2**. A CI step must not lose its history silently, and 2 still blocks the release.
- In that case `--json` may say `"PASS"` while the process exits 2.

## History never chooses a baseline

A store is a record, not an input. It never picks a baseline, never promotes a candidate and never feeds a policy. Every comparison is still between two run directories you name.

## Sharing a store

- **One writer at a time.** EvalGate doesn't lock the store. Two processes appending at the same moment can pick the same id, and one entry is lost. A report file swapped by such a race is caught by its hash in `history show`. Append from one job or branch at a time.
- **In CI,** keep the store as a build artifact of your main branch (download it, append, upload it) rather than committing it from several branches. Committed stores from parallel branches merge into duplicate ids, which EvalGate then rejects.
- **Rerunning a job** records the same compare again. That's allowed: delete the duplicate line if you don't want it.
