# Report plugins

EvalGate ships terminal, JSON, HTML and Markdown reports. For anything else (JUnit XML for a CI test tab, a CSV for a spreadsheet, a chat message body, an internal template) you can write a **reporter**: a function in a Python package that turns a report into text or bytes. EvalGate calls it and writes the file.

```bash
pip install -e examples/reporter-plugin       # an example reporter called "csv"
evalgate reporters                            # what's installed (imports nothing)
evalgate compare evals/base evals/cand --policy evalgate.yaml --format csv=failing.csv
```

## Writing one

A reporter is a function `render(report: Report) -> str | bytes`. You register it under the entry point group `evalgate.reporters` in your package's `pyproject.toml`:

```toml
[project.entry-points."evalgate.reporters"]
junit = "my_package.evalgate_junit:render"
```

```python
from evalgate import Report

def render(report: Report) -> str:
    rows = report.comparison.candidate_failing_checks if report.comparison else []
    return "".join(f"{c.case_id},{c.check}\n" for c in rows)
```

**What the reporter returns:**
- **Text** is written as UTF-8, with no newline translation: your line endings are kept. Text that can't be encoded (for example with lone surrogates) is an error.
- **Bytes** are written unchanged.
- **Anything else** is an error.

**Files:** EvalGate writes the file atomically. Your function shouldn't write files itself.

**When it is called:** for every candidate, including INVALID ones, where `report.comparison`, `report.policy` or the manifests can be `None`.

**Names:**
- Names match exactly (case-sensitive), and a name can't contain `=`.
- If two installed packages register the same name, `--format` refuses it and names both packages. `evalgate reporters` lists both.

**What a reporter sees:** exactly what `--json` holds. That includes:
- the run manifests, with any extra fields you put in `run.json`;
- the policy, and the diagnostics, which can quote input values;
- case ids, check names, categories and numbers.

It never gets model outputs or prompts.

## Using one

```bash
evalgate compare BASE CAND --policy evalgate.yaml --format junit=junit.xml --format csv=fail.csv
evalgate compare BASE CAND1 CAND2 --policy evalgate.yaml --format junit=junit-{n}.xml
```

- **The argument:** `--format NAME=PATH` is repeatable. It is split at the first `=`, so the path may contain `=`. With several candidates, `PATH` must contain `{n}`, as `--json` does ([several candidates](candidates.md)).
- **Bad names:** an unknown or ambiguous name stops the command before anything is compared (exit 2). The message lists the installed reporters.
- **Order:**
  1. decide every candidate;
  2. write `--json`, `--report` and `--markdown`;
  3. write `--summary`;
  4. record `--history`;
  5. load and run the reporters, each loaded once, in `--format` order;
  6. print.

  Reporters run last, so a misbehaving one can't stop the built-in artifacts, the summary or history from being written.
- **Failure:** if a reporter fails to import, raises (including `SystemExit`) or returns something EvalGate can't write:
  - the decision is still printed;
  - later reporter files aren't written;
  - the command exits 2.

  The message gives the reporter's name and the exception type, but never the exception text, which could quote report data.
- **Debugging:** use the Python API, where your exception propagates unchanged:
  ```python
  import evalgate
  report = evalgate.compare("evals/base", "evals/cand", "evalgate.yaml")
  print(evalgate.reporters.render("junit", report))
  ```
  `evalgate.reporters.available()` lists the reporters, and `evalgate.reporters.ReporterError` is raised for an unknown or ambiguous name or a bad result.

## What EvalGate guarantees, and what it can't

A reporter is code from a package you installed, running in EvalGate's process.

**Guaranteed:**
- **Loading:**
  - EvalGate imports a reporter only when `--format` names it, and only after the decisions and every built-in write.
  - A plain `compare` doesn't even read the list of installed reporters.
  - EvalGate never loads code from a file path, from YAML or from the policy.
- **Exit code:** an exception, a `SystemExit` (even `sys.exit(0)`) or a bad return value from a reporter makes the command exit 2. A reporter can't turn a FAIL into exit 0 that way.
- **A copy of the report:** each reporter gets its own deep copy, so an accidental change can't affect the next reporter or the printed decision.

**Not preventable:** code running in the same process can deliberately interfere. It can:
- end the process with `os._exit`;
- monkeypatch EvalGate;
- start threads or register `atexit` handlers;
- write files of its own, or read anything you can.

Any installed package can also run code when Python starts (through a `.pth` file) without EvalGate being involved. Install reporters only from sources you'd trust with your CI.

## Exit codes and Ctrl-C

Exit codes are the decision's (0 PASS, 1 FAIL, 2 INVALID), or 2 when a reporter fails.

**Ctrl-C** while a command runs exits 2 (no decision). Before 0.14 it exited 1, the code for a policy FAIL. Two windows aren't covered:
- Ctrl-C while the command line is still being parsed exits 1, from Typer.
- During interpreter start-up, Python's own default applies.
