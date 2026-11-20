# Design: Phase 7 (local history, multiple candidates, failure groups, and the long tail)

Status: **0.13.0 implemented** (interactive report viewer and history page, see [docs/report.md](../report.md)); before that, **0.12.0 implemented** (local history, see [docs/history.md](../history.md); several candidates, see [docs/candidates.md](../candidates.md); failure groups, see [docs/report-format.md](../report-format.md#comparisonfailure_groups)); later releases planned. Each later release gets its own task breakdown in a plan PR when its turn comes, as was done for each Phase 6 slice.

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
| **0.13.0** | **Interactive report viewer:** the HTML report gains sorting, filtering and search with inline JS (still standalone, no CDN); `evalgate history report OUT.html` makes a static trend page with inline SVG | Works offline from `file://`, with no network requests, no `serve` command and no auto-refresh. No outputs are shown. It is a view of the JSON and never shows data that isn't in it. There is no embedded JSON: one hash-pinned inline script runs under a Content Security Policy (`default-src 'none'`). |
| **0.14.0** | **Report plugins:** `evalgate compare --format NAME=PATH` renders through a reporter registered by an installed package (entry point `evalgate.reporters`) | Plugin code is imported only when `--format` names it, after the decisions, the built-in artifacts, the summary and history. Each reporter gets a deep copy of the report. A reporter's exception, `SystemExit` or bad return value exits 2. There is no code loading from paths or YAML. Deliberate in-process interference (`os._exit`, monkeypatching) can't be prevented: trust stops at installed packages, and the docs say so. |
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
  - `--json`: `{"history_version": 1, "path": …, "kind": …, "points": [...], "skipped": n, "mixed_datasets": bool}`.
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


## 3. 0.11.0: more than two candidates

### Problem

Teams often have several release candidates (two prompt variants, three models) and want to know which of them are acceptable against the current baseline, and which is best on the metric they care about. Today that takes N separate `compare` calls and a spreadsheet.

### Decisions (defaults; object before T1 starts)

**Command**
- `evalgate compare BASELINE CANDIDATE... [options]` takes one or more candidates (`metavar` `CANDIDATE...`). Options may appear anywhere.
- A candidate path that starts with `-` needs `--` before it.
- A candidate may be the baseline itself; that is allowed.
- Each candidate is judged against the baseline by the existing engine (`api.compare`), with the same policy, so each gets exactly the report a two-run compare would.
  - The baseline and the policy are re-read for each candidate, behind a `shortcut:` comment. That keeps the reports byte-identical, and the cost is trivial.
- Candidates are never compared with each other, and none of them becomes a baseline.

**One candidate is unchanged.** Its artifacts, exit code, terminal output and history entry are byte-identical to 0.10, except:
- the `--help` and missing-argument texts, which now name `CANDIDATE...`;
- a literal `{n}` in a path, which stays literal;
- the `candidate n:` header and the final table, which appear only with `--rank-by`.

**Exit code** (fail closed)
- 2 if any candidate is INVALID, otherwise 1 if any FAILs, otherwise 0.
- "Did every candidate pass?" is the only question a single exit code can answer safely. Choosing among candidates is what the ranking and the summary are for.

**Artifacts** (one per candidate)
- With more than one candidate, the `--json`, `--report` and `--markdown` paths must contain `{n}`, the candidate's 1-based position on the command line.
  - The check runs on `str(path)` before any I/O. A missing `{n}` is a usage error (exit 2).
  - The substitution is `str(path).replace("{n}", str(n))`, applied to every occurrence and never via `str.format`. Other braces and Windows backslashes therefore stay as they are: `out/{run}-{n}.json` keeps `{run}`.
  - Parent directories are not created, as for every artifact today.
  - Each file is byte-identical to the two-run compare of that candidate.
- `--summary PATH` writes one JSON summary, for any number of candidates. Its path is never templated.
  - It is a Pydantic model (`MultiSummary` in `models/multi.py`) written with `write_json_report`: atomic, LF line endings, `indent=2`, fixed field order, and no timestamps, so the same inputs always give the same bytes.
  ```json
  {"summary_version": 1, "evalgate_version": "0.11.0", "baseline_run_id": "base",
   "exit_code": 1, "rank_by": "quality",
   "candidates": [{"n": 1, "run_id": "cand-a", "status": "FAIL"}, …],
   "ranking": [{"rank": 1, "n": 2, "run_id": "cand-b", "status": "PASS", "value": 0.91, "cases": 40}, …],
   "cases_differ": false}
  ```
  - `run_id` (or `baseline_run_id`) is `null` for a run that didn't load.
  - `ranking` is `null` without `--rank-by`, and `[]` when no candidate could be ranked.

**Order of operations and partial failure**
- The order:
  1. Decide every candidate.
  2. Write each candidate's artifacts, in order.
  3. Write `--summary`.
  4. Record history, in order.
  5. Render everything.
- **On a write failure** (an `OSError` on an artifact or the summary, or any history error):
  - writing stops at that point, so later candidates' artifacts and entries aren't written;
  - everything is still rendered, then the error is printed, and the command exits 2.
  - A store may therefore hold the entries before the failure. Each append is atomic, and the docs say so.
- `summary.exit_code` is the decision's exit code. The process can still exit 2 after a write failure, the same caveat as in 0.10.

**Ranking** (informational only)
- `--rank-by NAME` names one policy metric. The value ranked is each candidate's `candidate_mean` of that metric. The baseline is the same for every candidate, so this is equivalent to ranking by the change.
- **Which candidates are ranked:** only non-INVALID candidates that have the metric. FAIL candidates are included and shown as FAIL, because the ranking answers "which is best on this metric", not "which may ship".
- **Order:** sorted in the metric's declared direction (`maximize` puts the highest first). Ties keep command-line order. `rank` is the position, 1…k, with no shared ranks.
- **Different case counts:** each item carries `cases`, the metric's compared-case count. When the ranked candidates' `cases` differ, a warning is printed, because means over different case sets (for example, a candidate with errors on the hard cases) aren't directly comparable.
- **Unknown names:** if the policy loads and `NAME` isn't one of its metrics, that is a usage error (exit 2) before any comparison. If the policy fails to load, every report is INVALID anyway, the name isn't checked, and `ranking` is `[]`.
- There is no aggregate score and no weighting. The ranking never changes a status, the exit code or any report, and it is shown only in the terminal and the summary.

**History**
- `--history DIR` records one compare entry per candidate, in command-line order, all with the same `--label`. The label is what links entries from one multi-candidate run, and the docs say so.

**Terminal**
- With more than one candidate, or with `--rank-by`, each report is printed under a header `candidate n: <path>`.
- A final table follows: n, run_id, status and, with `--rank-by`, rank, value and cases.
- All user strings are escaped.

**SDK**
- `evalgate.compare_many(baseline, candidates: Sequence[str | Path], policy="evalgate.yaml", *, rank_by=None) -> MultiResult`.
- It raises `TypeError` if `candidates` is a `str` or a `Path` (iterating it would split the path into characters), and `ValueError` if it is empty or if `rank_by` is unknown to a policy that loaded.
- `MultiResult` is a frozen dataclass with:
  - `reports: list[Report]` (in order);
  - `exit_code: int`;
  - `ranking: list[RankItem] | None`;
  - `summary() -> MultiSummary`.
- The CLI is a thin wrapper.

### Tasks

```
T1 compare_many + ranking ──▶ T2 CLI ──▶ T3 docs + example + release
```

**T1. `compare_many` and ranking** (`api.py`, `comparison/ranking.py` pure, `models/multi.py`)
- Tests:
  - each report equals the two-run `compare` of that candidate;
  - the exit-code aggregation, for every mix of PASS, FAIL and INVALID;
  - ranking in both directions, and a tie under each direction;
  - INVALID is excluded and FAIL included;
  - every candidate INVALID gives `[]`, and no `rank_by` gives `None`;
  - different `cases` counts are flagged;
  - a candidate that doesn't load gives `run_id: None`;
  - a `str` passed as `candidates` raises `TypeError`, and an empty list raises `ValueError`;
  - an unknown `rank_by` with a valid policy raises, but not with an invalid policy;
  - the summary bytes are deterministic;
  - the same candidate given twice, and a candidate equal to the baseline.

**T2. CLI** (`cli.py`, `reporting/terminal.py`)
- Tests:
  - a single candidate is byte-identical to before: artifacts, exit code, terminal output and history;
  - one candidate with `{n}` in a path keeps it literal;
  - N candidates write N files;
  - `{n}` twice in a path, `{n}` in a directory part, other braces kept, and a Windows path;
  - a path without `{n}` exits 2 and writes nothing;
  - the exit codes;
  - `--summary` with 1 and with N candidates;
  - an unknown `--rank-by` exits 2;
  - `--rank-by` with an invalid policy;
  - a write failure on candidate 2 of 3, and a summary write failure: exit 2, everything rendered, and the earlier artifacts exist;
  - history records N entries in order;
  - options placed between the positional arguments;
  - escaping.

**T3. Docs and release** (README, `docs/` section, `examples/multi-candidate/`, CHANGELOG, `_version.py`)
- **Example:** one baseline and three candidates.
  - The candidate with the highest quality regresses on safety (FAIL), so `--rank-by quality` ranks it first while marking it FAIL. That shows the ranking isn't a decision.
  - The other two pass, with different quality.
- It is registered in `test_examples.py`.

**Out of 0.11.0:**
- comparing candidates with each other;
- picking a winner automatically;
- an aggregate score;
- combined multi-candidate HTML or Markdown reports (0.13 may add a combined view);
- statistics on differences between candidates.

## 4. 0.12.0: failure groups

### Problem

A report lists every failing `(case, check)` pair, but a reviewer wants the pattern: which checks fail, in which categories, whether the failures are new in this candidate or were already failing, and which cases keep failing release after release.

### Decisions (defaults; object before T1 starts)

**A `comparison.failure_groups` report section** (additive, so `report_version` stays `"1"`)
- **Population:** the candidate's failing checks over compared cases only, that is, the matched cases that are `ok` in both runs, after collapsing repeats.
  - This is narrower than `candidate_failing_checks`. That list also covers cases that errored in the baseline, and candidate-only cases when `require_same_cases: false`.
  - The groups are computed inside the existing `compare()` loop over those pairs, so one loop gives one population and one classification.
- **Classification:** each failing pair falls into exactly one bucket:
  - `new`: the check passes in the baseline (the same as `newly_failing_checks`);
  - `persistent`: it fails in the baseline too;
  - `no_baseline`: the baseline case doesn't have that check, as with a check only the candidate adds. Validation already rejects checks the candidate drops.
  - Every group satisfies `failing == new + persistent + no_baseline`.
- **Categories:** a case's category is its baseline `metadata.category` when that is a string, the same rule `categories[]` uses. Pairs whose case has none (including a category only on the candidate side, or a non-string one) are left out of the category groups and counted, as pairs, in `uncategorized`.
- **Shape:**
  ```json
  "failure_groups": {
    "by_check": [{"check": "safety_valid", "failing": 3, "new": 2, "persistent": 1, "no_baseline": 0, "cases": ["a", "b", "c"]}],
    "by_category": [{"category": "billing", "failing": 2, "new": 2, "persistent": 0, "no_baseline": 0, "checks": ["safety_valid"], "cases": ["a", "b"]}],
    "by_check_category": [{"check": "safety_valid", "category": "billing", "failing": 2, "new": 2, "persistent": 0, "no_baseline": 0, "cases": ["a", "b"]}],
    "uncategorized": 1
  }
  ```
  - In `by_check`, `failing == len(cases)` always; the field is kept for symmetry. In `by_category`, `failing` counts pairs and `cases` lists the distinct cases.
  - Every list is sorted by `failing` descending, then by its key(s) ascending. `cases` and `checks` are sorted.
  - Nothing is truncated in JSON. `report-format.md` notes that the lists repeat case ids already in the failing pairs.
- **When it's present:** `Comparison.failure_groups: FailureGroups | None = None`.
  - Every 0.12 report that has a comparison carries it (with empty lists when nothing fails).
  - It is `null` only in reports written before 0.12, which therefore still load. `history show` reads stored 0.10 and 0.11 reports.
- **Privacy:** case ids and check names only, which the report already exposes. No outputs, and no metadata beyond the category.
- **Reporting only.** No policy rule reads the groups, and decisions don't change.

**Renderers**
- **Terminal and Markdown:** a "failure groups" block lists the top 5 `by_check_category` groups (check, category, failing, new, persistent, no_baseline).
  - When no failing case has a category, it falls back to the top 5 of `by_check`, so a run without categories still shows its failures.
  - It is printed only when something fails, and never for an INVALID report (which may still carry a comparison).
- **HTML:** not in 0.12. The HTML report is being restyled outside this plan, and 0.13 rebuilds it as the interactive viewer, which will show all groups. The JSON carries everything in the meantime.

**History: entries gain case ids, under `history_version: 2`**
- **Version bump:** new entries are written with `history_version: 2`.
  - The reader accepts 1 and 2, so a store may mix them.
  - A 0.10 or 0.11 reader meeting a v2 line prints its existing, accurate "unsupported history_version" diagnostic, instead of reading an unknown field as corruption.
  - The `--json` envelopes of `list`, `show` and `trend` keep `history_version: 1`, since their shape is unchanged; `entries` may hold v2 lines.
- **`case_ids` field** (run entries only):
  - It is required on v2 run entries and not allowed on v1 lines or on compare entries.
  - It lists the ok cases after collapsing repeats, sorted.
  - A report doesn't list its compared cases, so compare entries have no `case_ids` and their `of` is `null`.
- **Compare entries' `failing_checks`** now come from `failure_groups.by_check`, that is, from compared cases only. This fixes 0.10 letting candidate-only cases into `failing_checks`. A stored report without `failure_groups` (written before 0.12) is summarised the 0.10 way and recorded as a v1 entry.
- **Privacy note:** `docs/history.md` gains a line saying v2 entries store every case id they cover. Ids can embed user data, and a larger index makes each append's rewrite slower.

**`evalgate history failures`**
- `evalgate history failures --history DIR [--kind run|compare] [--dataset ID[@VERSION]] [--application APP] [--last N] [--min K] [--json]`.
- **Selection:**
  - The same rules as `trend`: one kind (default `run`), the same filters, and `--last N` applied after filtering.
  - INVALID compare entries are excluded: they carry no data and would break streaks.
  - Entries without an identity never match `--application` or `--dataset`.
- **Per `(case_id, check)` pair:**
  - `failing`: the number of selected entries in which it fails;
  - `of`: the number of selected entries whose `case_ids` include the case. It counts entries containing the case, not entries that ran that check, and is `null` for `--kind compare`, or if any selected run entry is v1 (no `case_ids`). For run entries `failing <= of` always holds;
  - `first` and `last`: the ids of the first and last entries in which it fails;
  - `streak`: the number of most recent selected entries in which it fails, without a gap. Any selected entry where the pair doesn't fail ends the streak, including one that lacks the case or where it errored. The docs say a broken streak isn't proof of a fix.
- **Listing:** pairs with `failing >= K` (`--min`, default 2, at least 1; anything else exits 2), sorted by `streak` descending, then `failing` descending, then `case_id` and `check`.
- **Multi-candidate stores:** sibling entries from one 0.11 multi-candidate run count as separate entries. The docs say to narrow the selection with `--application`.
- **Output:**
  - A `mixed_datasets` warning, as for `trend`.
  - Exit 0, or 2 on bad input; never 1.
  - JSON: `{"history_version": 1, "kind", "entries": n, "pairs": [{case_id, check, failing, of, first, last, streak}], "mixed_datasets": bool}`.

### Tasks

```
T1 grouping in compare() + report field ──▶ T2 terminal + Markdown ──┐
T3 history v2, case_ids, failures (needs T1) ────────────────────────┴──▶ T4 docs + release
```

**T1. Grouping** (`comparison/engine.py`, `models/report.py`, `report-format.md`)
- Tests:
  - hand-built runs give exact groups;
  - every bucket, and the invariant;
  - consistency with the engine: `by_check[c].new == check_stats[c].newly_failing`, and `failing - no_baseline == check_stats[c].candidate_failures`;
  - a case errored in the baseline is excluded, and so is a candidate-only case under `require_same_cases: false`;
  - a candidate-only check goes to `no_baseline`;
  - baseline categories, with candidate-only and non-string categories counted in `uncategorized` (as pairs);
  - ties sort by key;
  - nothing failing gives empty lists;
  - repeats are collapsed first;
  - a 0.11 report JSON (no field) still loads.

**T2. Terminal and Markdown** (`reporting/terminal.py`, `reporting/markdown.py`)
- Tests:
  - the top 5 with counts;
  - the `by_check` fallback without categories;
  - nothing shown when nothing fails, or for an INVALID report;
  - escaping.

**T3. History** (`models/history.py`, `comparison/history.py`, `io/history.py`, `cli.py`)
- Changes: v2 writing; mixed v1/v2 reading; `case_ids`; compare `failing_checks` from the groups; a pure `recurring_failures(entries, …)`; the CLI command.
- Tests:
  - hand-computed `failing`, `of`, `first`, `last` and `streak`, including the latest entry lacking the case;
  - an INVALID compare in the window is ignored;
  - a mixed v1/v2 store gives `of: null`;
  - `case_ids` on a v1 line or on a compare entry is rejected, and is required on a v2 run entry;
  - compare entries' `of` is `null`, and their `failing_checks` exclude candidate-only cases;
  - `failing <= of` for run entries;
  - `--min 0` exits 2;
  - kind and dataset selection, and `--last`;
  - JSON shape, exit codes, escaping;
  - `history show` of a stored 0.11 report.

**T4. Docs and release** (`docs/history.md`, `docs/report-format.md`, README, example, CHANGELOG, `_version.py`)
- The example extends `examples/history` with a third run, so one pair fails twice in a row.
- The CHANGELOG notes `history_version: 2` and the compare `failing_checks` fix.

**Out of 0.12.0:**
- HTML rendering of groups (0.13);
- clustering by text similarity or by outputs (never: outputs aren't stored);
- policy rules on groups;
- a recurring-failure gate;
- `compared_case_ids` in the report.

## 5. 0.13.0: interactive report viewer

### Problem

The HTML report is a static page: long case lists can't be sorted or searched, and the failure groups from 0.12 aren't in it at all. History has no visual view either. Teams open the JSON instead, which defeats the point of a readable report.

### Decisions (defaults; object before T1 starts)

**Still one offline file.** The report stays a single standalone `.html`:
- no CDN, no web font download, no network request of any kind, and no `serve` command;
- it opens from `file://` and works in an email attachment or a CI artifact viewer.
"Interactive" means sorting, filtering and folding inside that file, never a dashboard. That keeps the out-of-scope line ("dashboards, hosted anything").

**Built on the restyle (T0).** The EvalGate look is landed first, unchanged: the monospace terminal style with dark and light themes, and the `decision: FAIL` header with its exit code. 0.13 adds behaviour to that design and doesn't restyle it again.

**A view of the JSON, never more.**
- Every value in the HTML comes from the JSON report. The page shows no data that isn't there, and drops nothing a reader needs to act on.
- To meet that, 0.13 adds the sections the HTML doesn't have today:
  - the baseline and candidate errored case ids (today only their counts appear);
  - `candidate_failing_checks`;
  - the failure groups.
- The HTML has no size caps today, and it gains none.

**There is no embedded JSON.** The script works only on the DOM: it reads cells and moves rows. That avoids a second copy of the data and the `</script>` escaping problem altogether. Section 1's 0.13 row is amended to match.

**Progressive enhancement**
- The server-rendered HTML is complete without JavaScript:
  - every table emits all its rows, with `<thead>` and `<tbody>`;
  - long sections are `<details open>`, open so find-in-page and printing see them, and they fold without script.
- The script adds three things:
  - **Sorting:** a `<button>` inside each sortable `<th>`, with `aria-sort`, reachable by keyboard.
  - **Filtering:** a filter `<input>` above each long table, created by the script, so readers without JavaScript never see a control that does nothing.
  - **Number of rows shown:** a "n of m rows" note while filtering.
- **Sort rules:**
  - numeric cells carry `data-sort` with the full-precision value (`repr(float)`), not the 6-significant-digit display;
  - CI cells sort by their lower bound;
  - `n/a` and empty cells always sort last, in both directions;
  - text compares by code unit (`<`), never `localeCompare`, so the order doesn't depend on the browser's locale;
  - the sort is stable.
- No inline `on*=` handlers anywhere: everything is wired with `addEventListener`. A hash doesn't allow inline handlers without `'unsafe-hashes'`, so an injected `onerror` stays inert.

**Content Security Policy**
- The page's `<meta charset>` is followed directly by:
  ```html
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-<B64>'; img-src data:; base-uri 'none'; form-action 'none'">
  <link rel="icon" href="data:,">
  ```
  It comes before any `<style>` or `<script>`. The `data:,` icon stops a blocked `/favicon.ico` request (a console error) when an artifact viewer serves the page over http.
- `<B64>` is `base64(sha256(UTF-8 bytes of the exact text between <script> and </script>))`. The script is ASCII-only, and there is exactly one script block.
- `default-src 'none'` blocks every fetch, frame, font and connection.
- `style-src 'unsafe-inline'` lets an injected style attribute apply, which is harmless: CSS can't load anything under `default-src 'none'`.
- The policy is set by a `<meta>` tag, which applies on `file://`. Every directive used is allowed in a meta CSP.

**The script**
- It lives in `src/evalgate/reporting/viewer.js`, read through `importlib.resources`. Hatch already ships non-Python files under `src/evalgate`.
- It is vanilla JS, about 100 lines, with no dependencies, and it binds only to `data-*` hooks.
- It doesn't touch `document` at top level, only inside an init function called at the end, so Node can load it to test the comparator.
- Forbidden in its source, and checked by a test: `fetch`, `XMLHttpRequest`, `import(`, `eval`, `Function(`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`.

**Determinism.** The same report gives the same bytes:
- the script is a constant and its hash is computed from it;
- numbers use fixed formats;
- there are no timestamps or random ids (and no generation timestamp on the history page).

**`evalgate history report OUT.html`**
- `evalgate history report OUT.html --history DIR [--kind run|compare] [--dataset …] [--application …] [--last N] [--path PATH …]`. It writes through the existing atomic LF writer; a write failure exits 2.
- **Selection:** one pure helper, `select_entries(entries, kind, dataset, application, last)` in `comparison/history.py`, shared by `recurring_failures` and the page.
  - It excludes INVALID compares, as `recurring_failures` already does.
  - `trend` keeps its own selection code (the helper always drops INVALID compares, which `trend` counts as skipped), so its output doesn't change. `docs/history.md` notes that the page leaves out INVALID compares that `history trend` lists.
  - `--last` applies to the selected entries, not to points. Every chart and table on the page covers the same entries.
- **Paths charted:** with no `--path`, the sorted union of `means` keys (run entries) or `metrics[].path` (compare entries). A `--path` with no values in the selected entries gets a chart box saying "no values for this path".
- **Page content:**
  - an entries table: id, label, status, version, dataset, recorded_at;
  - one SVG chart per path;
  - the recurring failures table (`recurring_failures` over the same entries);
  - the mixed-datasets warning.
- **The SVG** is generated server-side with no chart library, and is byte-deterministic:
  - `viewBox="0 0 640 200"`, with 40 px padding and 80 px on the left for the y labels (as built; 40 clipped labels like `0.01275`);
  - x is the entry's index among the selected entries, evenly spaced and not by time. A single entry sits at the centre of the plot area;
  - y spans the minimum and maximum of that chart's values. When they are equal, or there is one point, the line is flat at mid-height;
  - coordinates are formatted `f"{v:.2f}"`;
  - an entry without the path is a gap. Each run of consecutive points is one `<polyline>`, and every point is also a `<circle>` with a `<title>` giving the id, label and full value;
  - the y labels are the minimum and maximum (6 significant digits). The x labels are only the first and last entry ids, which avoids overlap; full labels are in the titles and the entries table;
  - values are finite already (`Stat.value` is a finite `Number`), so non-finite values can't occur.
- **Exit codes:** 0, or 2 for a missing or corrupt store or a write failure. An empty selection still writes a page that says so, with exit 0.

**Escaping.** Every user string goes through `html.escape` with `quote=True`. That covers labels, case ids, check and category names, run ids, metric paths, the SVG `<title>` text and every attribute. Tests check hostile names everywhere they appear.

### Tasks

```
T0 land the restyle ──▶ T1 viewer (CSP, script, sections) ──▶ T2 history page ──▶ T3 docs + release
```

**T0. Land the HTML restyle**
- It is cut from `origin/main`, taking only the author's `html.py` diff and its CHANGELOG line under a new 0.13.0 (unreleased) section. `site/` is excluded.
- A test asserts the header (`decision:`, status, `exit N`) for PASS, FAIL and INVALID.

**T1. Viewer** (`reporting/html.py`, `reporting/viewer.js`)
- **Tests:**
  - **CSP:** the meta is present right after `<meta charset>`. The test extracts the script text from the output and hashes it itself, then checks that the hash matches the CSP.
  - **Markup checks parse the output** with `html.parser.HTMLParser`, never substring scans, because escaped hostile text such as `&lt;img onerror=…&gt;` still contains `onerror=`. They check:
    - exactly one `<script>` element;
    - no attribute name starting with `on`;
    - every `src`, `href`, `action` and `srcset` value starts with `data:`;
    - `url(` and `@import` appear only inside `<style>`.

    URL-looking user text, such as a run_id containing `https://x`, is allowed in escaped cells.
  - **The script source** contains none of the forbidden tokens.
  - **No-JS completeness:** every row is present in the raw HTML, and the new sections (errored ids, `candidate_failing_checks`, failure groups) match the JSON. Failure groups are absent for INVALID or when nothing fails.
  - **`data-sort`** holds the full-precision numbers.
  - **Hostile names** (`</script>`, `<img onerror=…>`, quotes, `&`) are escaped in cells and attributes.
  - **Determinism:** a golden-file test for the quickstart report, and two renders in subprocesses with different `PYTHONHASHSEED` give the same bytes.
- **Node test**, skipped when `node` is missing: it loads `viewer.js` with `vm` and checks the comparator on numbers, text, `n/a` and empty cells, ties, and both directions.
- DOM behaviour (clicks and filtering) has no automated test. The comparator does, and T3 checks the page by hand.

**T2. History page** (`reporting/history_html.py`, `comparison/history.py` `select_entries`, `cli.py`)
- **Tests:**
  - `select_entries` gives the same selections as before for `trend` and `recurring_failures`, which stay unchanged;
  - exact SVG coordinates for a hand-built store: two points, three points with a gap, one point, and equal values;
  - a `--path` with no values;
  - x labels only at the ends;
  - the failures table equals `recurring_failures` over the same entries;
  - the mixed-datasets warning;
  - hostile labels, paths and titles;
  - the CSP and no-external-reference checks, as in T1;
  - deterministic bytes with fixed `recorded_at`;
  - the exit codes, including an empty selection and a write failure.

**T3. Docs and release**
- `docs/report.md` (new): what the HTML report shows, and the viewer.
- A section in `docs/history.md`, and the README.
- `examples/history` also writes `history.html`.
- CHANGELOG, and the version bump to 0.13.0.
- Before the tag, a manual check in Chromium and Firefox (Safari if available): open both pages from `file://`, sort, filter and fold, and see no console errors. Then one more pass with JavaScript disabled.

**Out of 0.13.0:**
- a combined multi-candidate HTML (still one file per candidate);
- a served or live-updating dashboard;
- charts in the comparison report itself;
- embedding the JSON in the HTML;
- web fonts;
- theming options beyond the OS light and dark preference.

## 6. 0.14.0: report plugins

### Problem

Teams want report formats EvalGate doesn't ship: JUnit XML for a CI test tab, a CSV of failing checks for a spreadsheet, a Slack-ready message body, an internal template. Today they post-process the JSON report with their own scripts. That works, but each team re-parses the same model and the extra step isn't part of `compare`.

### Decisions (defaults; object before T1 starts)

**What a reporter is**
- A reporter is a function `render(report: Report) -> str | bytes`, exposed by an installed Python package under the entry point group `evalgate.reporters`:
  ```toml
  [project.entry-points."evalgate.reporters"]
  junit = "my_package.evalgate_junit:render"
  ```
- It sees exactly what `--json` holds:
  - manifests, including any extra fields from `run.json`;
  - the policy, and diagnostics, which can quote input values;
  - case ids, check names, categories, numbers and failure groups.

  It never gets case outputs. The docs say so. A reporter can still read anything the user can, so "no outputs" holds only for EvalGate's own artifacts.
- **Return value:** EvalGate writes what `render` returns, atomically.
  - A `str` is encoded as UTF-8 with no newline translation: whatever line endings it has are kept.
  - `bytes` are written unchanged.
  - The function does no file I/O of its own.
- **Reporter errors:**
  - a return value that is neither `str` nor `bytes`;
  - a `str` that can't be encoded (for example one with lone surrogates).

**Using one**
- `evalgate compare BASELINE CANDIDATE... --format NAME=PATH`, repeatable.
- `NAME=PATH` is split at the first `=`, so the path may contain `=`, and a reporter whose name contains `=` can't be selected. An empty name or path, or no `=`, is a usage error.
- With several candidates, `PATH` must contain `{n}`, with the same rules and substitution as `--json` (section 3).
- Paths that collide with each other or with `--json` are overwritten in order, as built-in artifacts already are.
- Reporters run for every candidate, including INVALID ones. There, `comparison`, `policy` or the manifests may be `None`, which a JUnit-style reporter needs in order to show INVALID.
- `evalgate reporters` lists the installed reporters: name, distribution, version and the `module:function` target.
  - It reads metadata only and imports nothing.
  - Names registered twice are listed twice, with exit 0.
  - With nothing installed it says so, also with exit 0.
  - Every value is escaped.

**Names and lookup**
- Names match exactly (case-sensitive).
- The lookup is `entry_points(group="evalgate.reporters").select(name=NAME)`, never `EntryPoints[name]`, which silently returns the first match.
  - `entry_points()` already deduplicates distributions by normalised name (the first on `sys.path` wins), so an editable install shadowing a site-packages copy isn't ambiguous.
  - More than one match after that is an error that names every distribution involved.
- An unknown name is a usage error (exit 2) before any comparison, and the message lists the installed names. An ambiguous name is checked at the same point, so a bad `--format` never leaves artifacts half-written.
- A plain `compare` without `--format` doesn't even read entry-point metadata.

**When plugin code runs**
- **Before comparing:** the name check reads metadata only; no plugin code is imported yet.
- **Loading:** `EntryPoint.load()` happens only once writing reaches the plugins. Each reporter is loaded once and reused for every candidate.
- **Order of operations** (extends section 3): trusted writes first, then untrusted code, then rendering.
  1. Decide every candidate.
  2. Write the built-in artifacts.
  3. Write `--summary`.
  4. Record history.
  5. Load and run the reporters, for each candidate in `--format` order.
  6. Render.
- **Departure from 0.11:** a reporter failure therefore can't stop the summary or the history entry from being written. Any failure still stops the writes that follow, everything is still rendered, and the command exits 2.

**What EvalGate guarantees, and what it can't**
- **Guaranteed:**
  - EvalGate never imports plugin code unless `--format` names it, and never loads code from a path, from YAML or from the policy.
  - Any `BaseException` except `KeyboardInterrupt` (so including `SystemExit`), or a wrong return value, raised while a reporter is imported or called, can't change the exit code. Any of them gives exit 2, with the reporter's name and the exception type. The exception message isn't printed, because it could quote report data.
  - Each reporter gets `report.model_copy(deep=True)`, so an accidental mutation can't affect the next reporter or the terminal output.
  - A `KeyboardInterrupt` inside a reporter is re-raised, so Ctrl-C stops at once. `_fail_closed` then exits 2 (below).
- **Not preventable:** code running in the same process can deliberately interfere: `os._exit`, monkeypatching EvalGate, threads, `atexit` handlers, its own file writes. Any installed package can also run code at interpreter startup (a `.pth` file) without EvalGate's involvement. Trust stops at the packages you install; the docs say so plainly.
- A reporter may raise `ReporterError` itself and look like an EvalGate error. That is accepted and documented.

**Ctrl-C** (an existing gap the plugin work touches)
- **The problem:** Typer, like Click's standalone mode, turns a `KeyboardInterrupt` inside a command into `Abort`, which prints "Aborted!" and exits 1, the code for a policy FAIL.
- **The fix:** `_fail_closed` gains a clause, after the `typer.Exit` re-raise, that maps `KeyboardInterrupt` and `typer.Abort` to exit 2. No command prompts, so this hides nothing.
- **Why 2, not 130:** exit 2 is chosen deliberately over the 130 convention, because 2 is EvalGate's code for "no decision".
- **Remaining windows:** this covers the command body only. Ctrl-C during argument parsing or the app callback still exits 1 through Typer. During interpreter start or imports, Python's default applies (130 on POSIX, 0xC000013A on Windows). The docs name these windows.
- It is listed under Changed.

**SDK** (`evalgate.reporters`, module `io/reporters.py`)
- `available() -> list[ReporterInfo]`.
- `render(name, report) -> str | bytes`:
  - it loads the reporter and calls it with a deep copy, checking the return type;
  - it raises `ReporterError` (a `ValueError`) for an unknown or ambiguous name or a bad return;
  - the reporter's own exceptions propagate unchanged, which is how authors debug, since the CLI hides the message.
- There is no SDK `write()`: callers write `render()`'s result themselves.
- `io/writer.py` gains `_atomic_write_bytes`; the CLI uses it for both `str` (encoded) and `bytes`.
- An isolation test checks that `api`, `engine`, `comparison` and `policies` never import `io.reporters`.

**Out of 0.14.0:**
- reporters for the history page or `history report`;
- reporters receiving anything other than one `Report`;
- options passed to reporters (a reporter reads its own environment variables or config file);
- built-in formats such as JUnit, which can ship later as an ordinary reporter inside EvalGate if asked;
- sandboxing or running reporters in a subprocess;
- a scripts wrapper that would also cover Ctrl-C during argument parsing.

### Tasks

```
T1 registry + SDK + `evalgate reporters` ──▶ T2 compare --format + Ctrl-C ──▶ T3 docs + example + release
```

**T1. Registry and SDK** (`io/reporters.py`, `io/writer.py` bytes writer, `cli.py` `reporters` command)
- **Test approach:** tests monkeypatch `importlib.metadata.distributions` itself, which `entry_points()` calls internally, so its own dedupe is exercised, with a small `importlib.metadata.Distribution` subclass. Its `read_text("METADATA")` returns a name and version, and its `read_text("entry_points.txt")` returns the entry-point INI pointing at functions in a test module. `EntryPoint.dist` is then set through the public API, on 3.11 too, and nothing is installed. T1 confirms the 3.11 and 3.13 `importlib.metadata` behaviour this relies on.
- **Tests:**
  - `available()` lists reporters sorted, without importing them (a module that raises on import still lists);
  - a duplicate name is listed twice;
  - `render` passes a deep copy (a mutating reporter leaves the caller's report unchanged), and accepts `str` and `bytes`;
  - any other return type raises `ReporterError`;
  - an unknown name raises `ReporterError` with the installed names, and an ambiguous one with both distributions;
  - the same distribution appearing twice (editable plus installed) isn't ambiguous;
  - the bytes writer is atomic and writes bytes unchanged;
  - `evalgate reporters` with reporters, with none (exit 0), and with escaping;
  - the isolation test.

**T2. `compare --format`** (`cli.py`, `_fail_closed`)
- **Tests:**
  - **Basic use:** a single candidate writes the file and keeps the decision's exit code (0 and 1); several candidates with `{n}`, and without it (a usage error).
  - **Bad arguments:** malformed values (`junit`, `=x`, `junit=`), and a path containing `=`; an unknown name exits 2 before comparing and writes nothing.
  - **Loading:**
    - `--format a=…` doesn't import reporter `b`;
    - a plain `compare` doesn't import a fake reporter module whose import would fail;
    - a reporter's import-time code runs only after the built-in artifacts, the summary and the history entry exist.
  - **Failures:**
    - a reporter raising `ValueError`, or returning `None`, or a `str` with a lone surrogate: exit 2, everything rendered, the earlier artifacts present, the later reporter outputs absent, and the exception message not printed;
    - `SystemExit(0)` on a FAIL decision exits 2;
    - a `KeyboardInterrupt` from a reporter, and one from the command body, both exit 2, not 1.
  - **Mutation:** reporter 1 mutates its report; reporter 2 and the terminal see the original.
  - **INVALID:** a reporter is called for an INVALID candidate with `comparison=None`.

**T3. Docs and release**
- **Docs:** `docs/reporters.md` (new) covers:
  - writing a reporter and packaging it with its entry point;
  - what a reporter sees (the same as `--json`), and the trust model, including what can't be prevented;
  - the failure behaviour, and debugging through SDK `render()`.

  Also the README, and a CLI section.
- **Example:** `examples/reporter-plugin/`, a minimal installable package (`pyproject.toml` plus one module) whose `csv` reporter writes the candidate's failing checks as CSV.
  - A test calls its function directly and checks the CSV against `candidate_failing_checks`.
  - The README shows `pip install -e examples/reporter-plugin` and `evalgate compare … --format csv=fail.csv`.
- CHANGELOG 0.14.0, including the Ctrl-C change under Changed, and the version bump to 0.14.0.
