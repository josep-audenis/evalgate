# Contributing

Thanks for helping. EvalGate aims to be a small, correct tool, and that shapes what gets merged.

## Setup

```bash
uv sync
git config core.hooksPath .githooks   # enables the pre-push checks
```

There is no hosted CI. The `pre-push` hook runs the same checks a CI job would, and blocks the push if any fail:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

The hook runs one Python version. EvalGate supports 3.11 and newer, so before a release also run the tests on the oldest and newest supported versions, for example `uv run --python 3.11 pytest` and `uv run --python 3.13 pytest`. `test_bootstrap_golden_values_same_on_every_python` fails if a Python version changes the bootstrap's resampling, and with it the decisions.

## Ground rules

- **Fail closed.** Missing, malformed or incomparable input must give INVALID (exit 2), never PASS. Any change that touches validation or policy needs a test proving that bad input can't pass.
- **Explicit semantics.** Metrics declare a direction. Never divide by a zero baseline. No weighted overall scores.
- **Deterministic.** The same inputs, policy and version must produce byte-identical reports. Sort every list by stable keys, and keep timestamps out of results.
- **Offline core.** No network calls, LLM APIs or databases in the core.
- **Layering.** `comparison/` and `policies/` are pure functions over models. File I/O lives in `io/`. The CLI and SDK both go through `evalgate.api.compare`, so there is never a second code path.
- **Small.** Add a module or option only when real code needs it. New dependencies need a strong reason.

## Where things live

```text
src/evalgate/
  models/       Pydantic models: run, case, policy, report
  io/           read run dirs and policies; write reports; EvalRun recorder
  validation.py comparability checks -> diagnostics
  comparison/   matching and metric deltas (pure)
  policies/     rule evaluation (pure)
  engine.py     validate -> compare -> decide
  api.py        path-based entry point shared by CLI and SDK
  reporting/    terminal and HTML renderers
```

The product spec is [docs/SPEC.md](docs/SPEC.md), and the JSON report contract is [docs/report-format.md](docs/report-format.md). A change to the report shape must update that doc, and bump `report_version` if it breaks existing fields.

## Pull requests

- One topic per PR, with tests. A bug fix starts with a test that fails without the fix.
- Explain *why* in the description, especially any change to decision semantics.
- To release, bump `src/evalgate/_version.py`. It is the only place the version lives.
