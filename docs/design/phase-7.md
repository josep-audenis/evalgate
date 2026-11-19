# Design: Phase 7 (local history, multiple candidates, failure groups, and the long tail)

Status: **planned.** Section 2 has the 0.10.0 task breakdown. Each later release gets its own task breakdown in a plan PR when its turn comes, as was done for each Phase 6 slice.

SPEC Phase 7 lists exploration items, not commitments. This doc puts them in order and keeps every one within the project's rules:
- **Offline core.** Nothing in `comparison/`, `policies/`, `validation.py`, `engine.py` or `api.py` touches the network or a database.
- **Deterministic decisions** with exit codes 0/1/2.
- **No raw outputs** in any report or store.
- **No hosted anything.** Every "viewer" is a static local file.
- **The baseline is never chosen automatically.**

## 1. Releases

| Release | Scope | Acceptance |
|---|---|---|
| **0.10.0** | **Local history:** record compare results and run summaries in a local append-only store; `evalgate history list / show / trend` | Recording never changes a decision or an exit code; the store holds no outputs; a corrupt store is reported line by line (exit 2); the order is stable (by entry id). |
| **0.11.0** | **More than two candidates:** `evalgate compare BASELINE CAND1 CAND2 …` judges each candidate against the baseline with the same engine, plus an optional ranking on one declared metric | Each candidate's report is byte-identical to a two-run compare; any INVALID input exits 2; the ranking is deterministic and uses no aggregate score. |
| **0.12.0** | **Failure groups:** a `failure_groups` report section (by check, category, and check × category; new vs persistent), and `evalgate history failures` for case/check pairs that keep failing across recorded entries | Reporting only: decisions are unchanged and the section is additive (`report_version` stays `"1"`); groups are sorted deterministically. |
| **0.13.0** | **Interactive report viewer:** the HTML report gains sorting, filtering and search with inline JS (still standalone, no CDN); `evalgate history report OUT.html` makes a static trend page with inline SVG | Works offline from `file://`; no network requests; no outputs shown; same data as the JSON. |
| **0.14.0** | **Report plugins:** `evalgate compare --format NAME=PATH` renders through a reporter registered by an installed package (entry point `evalgate.reporters`) | A plugin gets a read-only report and can't change the decision; a plugin failure exits 2; there is no code loading from paths or YAML. |
| **0.15.0** | **Experiment tracker integration:** opt-in SDK functions (`evalgate.integrations.mlflow` / `wandb`) that log a report to the user's own tracker library, each with its own extra | The engine never imports them (tested); network access goes only through the user's tracker library, as with judges; no outputs are logged. |
| **0.16.0** | **TypeScript SDK:** a package in `sdk/typescript/` that writes run directories (`run.json` + `cases.jsonl`, schema 1.0/1.1) and types the report JSON; comparison runs through the `evalgate` CLI | The TS writer produces byte-identical files to `EvalRun` for the same inputs (a conformance test); there is no TS copy of the engine. |

Pairwise judging stays deferred (native-evaluators design, section 6). It would get its own slice later.

**Ordering:**
- History (0.10) comes first, because 0.12 (recurring failures) and 0.13 (trend page) read it.
- 0.11 is independent of history, but it shares the CLI compare path with it, so it comes next.
- The long-tail items follow, in the order the user listed them.

Each release is published only when the user says so.

## 2. 0.10.0: local history

### Problem

`evalgate compare` answers one question about one pair of runs. Teams also want to know how quality has moved over the last N releases, and when a case started failing. Today the answers live in scattered JSON reports, if they were kept at all.

### Decisions (defaults; object before T1 starts)

**The store**
- **What it is:** a directory chosen by the user, `--history DIR`. Nothing is recorded unless the flag is passed, and there is no default location.
- **Layout:**
  ```
  DIR/
    index.jsonl          one entry per line, append-only
    reports/000003.json  the full JSON report of compare entry 3, byte-identical to --json
  ```
- **Entries are summaries, not copies of runs:**
  - run identity (`run_id`, `application`, `application_version`, `dataset_id`, `dataset_version`);
  - status and per-metric means;
  - the failing `(case_id, check)` pairs.
  - `Report` already never carries outputs, and a run summary is built from scores, metrics and checks only, so no output, prompt or `case` data can reach the store. Paths to run directories are not stored either.
- **Entry kinds** (`kind` is the discriminator):
  - `compare`: written by `evalgate compare … --history DIR`. It holds the status (PASS / FAIL / INVALID), the baseline and candidate identities, and one metric row per policy metric (`path`, `direction`, `baseline_mean`, `candidate_mean`, `delta_abs`). It also holds the candidate's failing check pairs, and the report id under `reports/`. An INVALID compare is recorded too, with no metrics, because "this pair couldn't be compared" is history worth keeping.
  - `run`: written by `evalgate history add RUN_DIR --history DIR`. It holds the run identity, `cases`, `errors`, and the means of every `scores.*` and `metrics.*` path over ok cases. Repeats are collapsed first with the existing `collapse`. It also holds check pass rates and the failing check pairs. An INVALID run (by `validate_run`) is not recorded, and the command exits 2.
- **Ids and order:** `id` is a sequence number, 1 for the first line, then 2, and so on. Every listing is in id order, which is recording order, never wall-clock order. `recorded_at` (UTC ISO-8601, seconds) is stored as metadata only. Nothing sorts or decides on it.
- **Format:** each line carries `history_version: 1` and is strict JSON (`loads_strict`). An optional `label` (free text, `--label`) names an entry, for example a release tag. It is escaped in every renderer.

**Recording**
- **Recording never changes a decision.** `compare --history` writes the entry after the decision and after the other artifacts (`--json`, `--report`, `--markdown`). The exit code is still 0/1/2 from the decision.
- **If the store can't be written** (bad directory, or a corrupt `index.jsonl` that makes the next id unknowable), the command exits 2 and prints the history diagnostics. That is the same rule as any other artifact failure: a CI step must not silently lose its history. Nothing is appended to a corrupt store.
- **Appending:** the report file is written first (atomically), then one complete line is appended to `index.jsonl` in a single write. A report file without an index line is harmless: it is never listed, and the next append overwrites it.
- **Concurrency:** two processes appending at the same moment can collide on an id. EvalGate is a local, single-user tool, so this is documented and not locked. The limit is marked with a `shortcut:` comment.

**Reading** (all reads are strict, and every bad line is reported by line number without values)
- `evalgate history list --history DIR [--last N] [--json]`: a table of id, kind, label, status, application_version and recorded_at.
- `evalgate history show ID --history DIR`: for a compare entry, renders the stored report with the existing terminal renderer. For a run entry, prints its summary.
- `evalgate history trend PATH --history DIR [--application APP] [--last N] [--json]`: the mean of one metric path per entry, in id order.
  - For a run entry the value is `means[PATH]`; for a compare entry it is the candidate mean of the policy metric with that path.
  - Entries without that path are skipped and counted.
- Exit codes: 0, or 2 for a missing or corrupt store or an unknown id. History commands never exit 1.
- **New codes:** `history_parse`, `history_record`, `history_missing` (no `index.jsonl`), `history_id` (unknown id or missing report file), `history_write`.

**What history never does**
- It never picks a baseline, never promotes a candidate, and never feeds a policy. Comparisons stay between two run directories the user names.

**SDK**
- `evalgate.history`: `record_report(report, dir, label=None) -> HistoryEntry`, `record_run(run_dir, dir, label=None)` and `read_history(dir) -> list[HistoryEntry]`.
- The CLI commands are thin wrappers over these, so the CLI and the SDK share one engine.

### Tasks

```
T1 models + pure summaries ──▶ T2 store (io) ──▶ T3 CLI ──▶ T4 docs + release
```

**T1. Models and pure summaries** (`models/history.py`, `history.py`)
- `HistoryEntry`: a discriminated union of `CompareEntry` and `RunEntry`. Both are strict, `extra="forbid"`, and have `history_version: Literal[1]`, `id: int >= 1`, `recorded_at` and an optional `label`.
- Pure functions:
  - `summarize_report(report) -> dict`, the compare fields;
  - `summarize_run(run) -> dict`, which collapses repeats first. A mean that overflows to inf makes the run unrecordable (`history_record` at add time), never an inf in the store.
- Tests:
  - a summary carries no outputs (sentinel stored with `store_output=True`);
  - the failing pairs are sorted;
  - means are hand-computed, including repeats;
  - an INVALID report gives an entry without metrics.

**T2. Store** (`io/history.py`)
- `append(dir, fields) -> HistoryEntry`:
  - finds the next id by reading the index strictly (a corrupt index means no append, `history_record` or `history_parse`);
  - writes `reports/<id>.json` atomically for compare entries;
  - appends one line.
- `read_history(dir)` is strict and reports every bad line.
- `record_report`, `record_run` and `load_entry_report(dir, id)` sit on top.
- Tests:
  - ids are sequential across appends;
  - a stored report is byte-identical to `--json`;
  - a corrupt line blocks the append and is reported by line;
  - a missing dir is created on first append, but a path that is a file gives `history_write`;
  - `history_missing` on read;
  - no `*.tmp` files are left behind;
  - no outputs are stored (sentinel).

**T3. CLI** (`cli.py`, `reporting/terminal.py`)
- `compare --history DIR [--label TEXT]`, plus `history add / list / show / trend`, as a Typer sub-app wrapped in `_fail_closed`.
- Tests:
  - each command's exit codes;
  - `compare --history` keeps exit 1 on FAIL and records it;
  - an INVALID compare is recorded and still exits 2;
  - a store write failure exits 2;
  - `--last`, `--application` and `--json` round-trip;
  - markup in a label or run_id is printed literally;
  - the help text lists the commands.

**T4. Docs and release** (new `docs/history.md`, `report-format.md` codes, README, `examples/`, CHANGELOG, `_version.py`)
- **Docs page:**
  - the layout, what is and isn't stored, and that ids are in recording order;
  - the concurrency caveat;
  - "history never picks a baseline";
  - keeping a store in CI (commit it, or keep it as a build artifact).
- **Example:** `examples/history/` builds a store from the existing example runs with a script. It is checked by a generator-parity test, with `recorded_at` fixed by a test hook.
- CHANGELOG 0.10.0, and the version bump to 0.10.0.

**Out of 0.10.0:**
- any automatic baseline;
- remote or shared stores;
- locking;
- deleting or editing entries (the store is append-only; to prune, edit the file yourself);
- charts (0.13.0) and failure trends (0.12.0).
