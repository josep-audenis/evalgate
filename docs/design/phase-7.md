# Design: Phase 7 (local history, multiple candidates, failure groups, and the long tail)

Status: **0.10.0 implemented** (local history, see [docs/history.md](../history.md)); later releases planned. Each later release gets its own task breakdown in a plan PR when its turn comes, as was done for each Phase 6 slice.

SPEC Phase 7 lists exploration items, not commitments. This doc puts the chosen ones in order, and each release after 0.10.0 is still gated on demand: if usage shows an item isn't needed, it is dropped.

Every release keeps the project's rules:
- **Offline core.** Nothing in `comparison/`, `policies/`, `validation.py`, `engine.py` or `api.py` touches the network or a database.
- **Deterministic decisions** with exit codes 0/1/2.
- **No raw outputs** in any report or store.
- **No hosted anything.** Every "viewer" is a static local file.
- **The baseline is never chosen automatically.**

**Not planned here:** SPEC Phase 7 also lists a dedicated GitHub Action and pull request comments. They were not chosen for this round. `--markdown` already writes a PR-comment body and `$GITHUB_STEP_SUMMARY`, and an Action is CI packaging around the existing CLI. Either can be picked up later on its own.

## 1. Releases

| Release | Scope | Acceptance |
|---|---|---|
| **0.10.0** | **Local history:** record compare results and run summaries in a local append-only store; `evalgate history list / show / trend` | Recording never changes the decision. A store failure is a tool error and exits 2, like any artifact write failure. The store holds what `--json` holds (no outputs). A corrupt store is reported line by line (exit 2). The order is stable (by entry id). |
| **0.11.0** | **More than two candidates:** `evalgate compare BASELINE CAND1 CAND2 …` judges each candidate against the baseline with the same engine, plus an optional ranking on one declared metric | Each candidate's report is byte-identical to a two-run compare, with one artifact file per candidate. Exit 2 if any candidate is INVALID, else 1 if any FAILs, else 0. The ranking covers only non-INVALID candidates, sorts on one policy metric with its declared direction, breaks ties by argument order, is informational only (never a decision, never a baseline), and uses no aggregate score. |
| **0.12.0** | **Failure groups:** a `failure_groups` report section (by check, category, and check × category; new vs persistent), and `evalgate history failures` for case/check pairs that keep failing across recorded entries of one kind and dataset | Reporting only: decisions are unchanged and the section is additive (`report_version` stays `"1"`); groups are sorted deterministically. |
| **0.13.0** | **Interactive report viewer:** the HTML report gains sorting, filtering and search with inline JS (still standalone, no CDN); `evalgate history report OUT.html` makes a static trend page with inline SVG | Works offline from `file://`, with no network requests, no `serve` command and no auto-refresh. No outputs are shown, and it holds the same data as the JSON. JSON embedded inline escapes `<`, `>` and `&` (as `\u003c`, `\u003e`, `\u0026`), so no label can break out of a `<script>`. |
| **0.14.0** | **Report plugins:** `evalgate compare --format NAME=PATH` renders through a reporter registered by an installed package (entry point `evalgate.reporters`) | Plugins run only after the exit code is computed, and get a deep copy of the report. Any `BaseException` a plugin raises (including `SystemExit`) is caught and exits 2. Entry points are resolved lazily, only when `--format` names one, so an installed package never runs code during a plain `compare`. There is no code loading from paths or YAML. `os._exit` can't be prevented: trust stops at installed packages, and the docs say so. |
| **0.15.0** | **Experiment tracker integration:** opt-in SDK functions (`evalgate.integrations.mlflow` / `wandb`) that log a report to the user's own tracker library, each with its own extra | `import evalgate` and the engine never import them (tested). Network access goes only through the user's tracker library, as with judges. No outputs are logged. Extra manifest fields are logged as tags, and the docs say so. |
| **0.16.0** | **TypeScript SDK:** a package in `sdk/typescript/` that writes run directories (`run.json` + `cases.jsonl`, schema 1.0/1.1) and types the report JSON; comparison runs through the `evalgate` CLI | Conformance: `load_run` on the TS output gives a `Run` equal to the Python `EvalRun` output for the same inputs. Bytes may differ, because JS and Python write numbers differently. A pytest test runs the TS writer through `node` and skips when Node is missing; the TS package has its own `node --test` suite. Report types are generated from the Pydantic JSON Schema. There is no TS copy of the engine. |

Pairwise judging stays deferred (native-evaluators design, section 6).

**Ordering:**
- History (0.10) comes first, because 0.12 (recurring failures) and 0.13 (trend page) read it.
- 0.11 is independent of history, but it shares the CLI compare path with it, so it comes next.
- The long-tail items follow.

Each release is published only when the user says so.

## 2. 0.10.0: local history

### Problem

`evalgate compare` answers one question about one pair of runs. Teams also want to know how quality has moved over the last N releases, and when a case started failing. Today the answers live in scattered JSON reports, if they were kept at all.

### Decisions (defaults; object before T1 starts)

**The store**
- **What it is:** a directory chosen by the user, `--history DIR`. Nothing is recorded unless the flag is passed, and there is no default location. The directory is created on first write (`parents=True`).
- **Layout:**
  ```
  DIR/
    index.jsonl          one entry per line, LF line endings, UTF-8
    reports/000003.json  the full JSON report of compare entry 3, byte-identical to --json
  ```
  Report files are named `f"{id:06d}.json"`, and the name grows wider past 999999.
- **What it holds:** exactly what `--json` holds, plus summaries.
  - That can include file paths and parser messages in the diagnostics of an INVALID report, and any extra fields a user put in `run.json`.
  - It never holds outputs, prompts or `case` data: `Report` never carries them, and a run summary is built from scores, metrics and checks only.
  - Run directory paths are not stored as fields.
- **Entry kinds** (`kind` is the discriminator):
  - `compare`: written by `evalgate compare … --history DIR`. It holds:
    - `status` (PASS / FAIL / INVALID);
    - `baseline` and `candidate` identities, each `run_id`, `application`, `application_version`, `dataset_id`, `dataset_version`. Both are `null` when that run failed to load;
    - one metric row per distinct policy metric path: `path`, `direction`, `baseline_mean`, `candidate_mean`, `delta_abs`, `cases` (= `compared_cases`). Empty when INVALID;
    - the candidate's failing `(case_id, check)` pairs (compared cases only, sorted);
    - `report_sha256`, the hash of the stored report file.
  - `run`: written by `evalgate history add RUN_DIR --history DIR`. It holds:
    - the run identity;
    - `cases`, counted after repeats are collapsed with the existing `collapse`, as `evalgate validate` prints them;
    - `errors`;
    - for every `scores.*` and `metrics.*` path, `{mean, cases}` over ok collapsed cases that have it;
    - for every check, `{pass_rate, cases}` over ok collapsed cases that have it;
    - the failing `(case_id, check)` pairs (sorted).
- **INVALID input:** an INVALID compare is recorded, because "this pair couldn't be compared" is history worth keeping. A run directory that doesn't load reports the existing reader codes (`run_dir_missing`, `file_missing`, …). An INVALID run (by `validate_run`) is not recorded, and `history add` exits 2: a run entry is only useful as a data point, and an invalid run has none.
- **Ids:**
  - The next id is the last line's id + 1, or 1 for an empty index.
  - The reader requires ids to be strictly increasing. Gaps are allowed, so pruning lines by hand is fine. A duplicate or decreasing id is `history_parse`.
  - Every listing is in id order, never wall-clock order.
  - `recorded_at` (UTC, ISO-8601, seconds) is metadata only. Nothing sorts or decides on it. The SDK takes `recorded_at=` (a `datetime`) for reproducible stores, such as the example and tests.
- **Format:** each line carries `history_version: 1` and is strict JSON (`loads_strict`). Any other version is `history_parse` with a clear message. An optional `label` (`--label`, up to 200 characters) names an entry, for example a release tag. It is escaped in every renderer, and control characters are shown literally.

**Recording**
- **Order in `compare --history`:**
  1. Decide.
  2. Write `--json`, `--report` and `--markdown`.
  3. Record the history entry.
  4. Render the decision in the terminal.
- **If recording fails** (unwritable directory, or a corrupt `index.jsonl` that leaves the next id unknown):
  - the decision is still rendered, then the history diagnostics are printed, and the command exits 2;
  - this is the same rule as any artifact write failure: a CI step must not silently lose its history, and exit 2 still blocks the release;
  - `--json` can then say `"FAIL"` or `"PASS"` while the process exits 2, and the docs say so;
  - nothing is appended to a corrupt store.
- **Atomic append:**
  - Read the index strictly, add the new line, and rewrite the whole file with the existing `_atomic_write` (temp file + `os.replace`).
  - A crash or a full disk therefore never leaves a torn line. The O(n) rewrite is trivial at this scale.
  - For a compare entry, the report file is written first, also atomically. A report file without an index line is never listed and is overwritten by the next append.
- **Concurrency** (`shortcut:` comment):
  - Two processes appending at once can pick the same id; the later rewrite wins, and one entry is lost.
  - `report_sha256` makes a swapped report file detectable: `history show` checks it and reports `history_id` on a mismatch.
  - The docs say a store must be appended from one job or branch at a time. For CI, they recommend keeping the store as a build artifact of the main branch, rather than committing it from several branches.
- **Duplicates:** rerunning the same CI job records the same compare again. That is allowed and documented, with no deduplication.

**Reading**
- All reads are strict. Every bad line is reported by line number, and Pydantic errors are shown as location and type only, never the input value.
- `evalgate history list --history DIR [--last N] [--json]`:
  - a table of id, kind, label, status (`valid` for run entries), application_version, dataset and recorded_at;
  - `--json`: `{"history_version": 1, "entries": [...]}`.
- `evalgate history show ID --history DIR [--json]`:
  - a compare entry renders its stored report with the existing terminal renderer, after checking `report_sha256`;
  - a run entry prints its identity, then a table of means and pass rates;
  - `--json` prints the entry, or for a compare entry `{"entry": …, "report": …}`.
- `evalgate history trend PATH --history DIR [--kind run|compare] [--dataset ID[@VERSION]] [--application APP] [--last N] [--json]`:
  - the mean of one metric path per entry, in id order;
  - `--kind` defaults to `run`. Kinds are never mixed, because a compare mean covers only the compared cases;
  - for a compare entry the value is the candidate mean for that path, and `--application` matches the candidate's application;
  - **datasets:** each point shows its dataset. Without `--dataset`, a trend that spans more than one `(dataset_id, dataset_version)` prints a warning, because means over different datasets aren't comparable;
  - entries without the path are skipped, and the skips are counted;
  - `--last N` applies after filtering and skipping;
  - `--json`: `{"history_version": 1, "path": …, "kind": …, "points": [...], "skipped": n}`.
- **Exit codes:** 0, or 2 for any diagnostic. History commands never exit 1.
- **Codes,** one meaning each:
  - `history_parse`: any bad index line, on read or before an append;
  - `history_record`: the input can't be summarised, e.g. an INVALID run or an overflowing mean;
  - `history_write`: an OS error while writing;
  - `history_missing`: no `index.jsonl`;
  - `history_id`: an unknown id, or a missing or mismatched report file.

**What history never does**
- It never picks a baseline, never promotes a candidate, and never feeds a policy. Comparisons stay between two run directories the user names.

**Module placement** (pure core, I/O in `io/`)
- Pure summaries live in `comparison/history.py`:
  - `summarize_report(report) -> dict`;
  - `summarize_run(run) -> dict`.
- Models live in `models/history.py`.
- I/O lives in `io/history.py`: `record_report(report, dir, *, label=None, recorded_at=None)`, `record_run(run_dir, dir, *, label=None, recorded_at=None)`, `read_history(dir)` and `load_entry_report(dir, id)`.
- The SDK functions are re-exported from `evalgate`, as `EvalRun` and `compare` are.
- The CLI commands are thin wrappers, so the CLI and the SDK share one engine.
- An engine-isolation test (like the judge one) checks that `engine`, `api`, `comparison` (apart from `comparison/history.py`) and `policies` never import `io.history`. It also checks that `comparison/history.py` itself imports nothing from `evalgate.io`.

### Tasks

```
T1 models + pure summaries ──▶ T2 store (io) ──▶ T3 CLI ──▶ T4 docs + release
```

**T1. Models and pure summaries** (`models/history.py`, `comparison/history.py`)
- `HistoryEntry`: a discriminated union of `CompareEntry` and `RunEntry`.
  - Both are strict, `extra="forbid"`, with `history_version: Literal[1]`, `id: int >= 1`, `recorded_at`, and an optional `label` (up to 200 characters).
  - Identities are optional on `CompareEntry`.
- `summarize_run` collapses repeats first. A mean that overflows to inf, or any non-finite value, raises `ValueError`, which the caller turns into `history_record`.
- Tests:
  - no outputs in a summary (a sentinel stored with `store_output=True`);
  - failing pairs are sorted;
  - means, case counts and pass rates are hand-computed, including repeats (collapsed `cases`);
  - sparse paths have their own `cases`;
  - an INVALID-on-load report gives an entry with null identities and no metrics;
  - two policy metrics on the same path give one row;
  - the purity and isolation test.

**T2. Store** (`io/history.py`)
- Append and read as described above.
- Tests:
  - ids are sequential;
  - after pruning a middle line or the tail, the next id is last + 1;
  - a duplicate or decreasing id, `history_version: 2`, a truncated last line, CRLF line endings and a missing trailing newline are each handled: CRLF and a missing final newline are read fine, and the rest are `history_parse`;
  - a corrupt index blocks the append;
  - a stored report is byte-identical to `--json`, and `report_sha256` matches;
  - a tampered report file gives `history_id` on load;
  - the directory is created; a path that is a file gives `history_write`;
  - `history_missing`;
  - no `*.tmp` files are left behind;
  - no outputs are stored (sentinel);
  - `recorded_at=` gives a reproducible file.

**T3. CLI** (`cli.py`, `reporting/terminal.py`)
- `compare --history DIR [--label TEXT]`, plus a `history` Typer sub-app with `add`, `list`, `show` and `trend`, all wrapped in `_fail_closed`.
- Tests:
  - exit codes for each command;
  - `compare --history` records PASS (exit 0) and FAIL (exit 1);
  - an INVALID-on-load compare is recorded and still exits 2;
  - PASS plus a store failure, and FAIL plus a store failure, both exit 2 with the decision still rendered;
  - trend: the kind, dataset and application filters, `--last` after filtering, the skip count, and the mixed-dataset warning;
  - `--json` shapes round-trip;
  - markup, newlines or control characters in a label or run_id are shown literally;
  - the help text lists the commands.

**T4. Docs and release** (new `docs/history.md`, `report-format.md` codes, README, `examples/`, CHANGELOG, `_version.py`)
- **Docs page:**
  - the layout, and what is and isn't stored (including paths in INVALID diagnostics, and manifest extras);
  - ids and pruning;
  - the concurrency caveat and the CI advice;
  - exit 2 on a store failure even after a PASS or FAIL decision;
  - "history never picks a baseline";
  - why trends don't mix kinds or datasets.
- **Example:** `examples/history/` builds a store from the existing example runs with a script that uses fixed `recorded_at` values. It is checked by a generator-parity test.
- CHANGELOG 0.10.0, and the version bump to 0.10.0.

**Out of 0.10.0:**
- any automatic baseline;
- remote or shared stores;
- locking;
- deleting or editing entries through EvalGate (the store is append-only; prune by hand);
- charts (0.13.0) and failure trends (0.12.0).
