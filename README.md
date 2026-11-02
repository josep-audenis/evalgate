# EvalGate

Local-first AI regression testing and release decision engine.

EvalGate compares a baseline evaluation run against a candidate, finds what improved and what regressed, applies your release policy, and returns an explainable **PASS / FAIL / INVALID** with CI-friendly exit codes (`0` / `1` / `2`). Fully offline, no LLM API key needed.

> Status: early development (Phase 1, core comparison engine). See [docs/SPEC.md](docs/SPEC.md).

## Usage

Each run is a directory with `run.json` (manifest) and `cases.jsonl` (one case per line). See [examples/quickstart](examples/quickstart) and the spec's data model (§5) for the format.

```bash
evalgate validate examples/quickstart/candidate
evalgate compare examples/quickstart/baseline examples/quickstart/candidate \
  --policy examples/quickstart/evalgate.yaml --json report.json
```

The quickstart candidate improves mean quality but newly fails `safety_valid` on one case, so the decision is **FAIL** (exit 1).

Policy notes:
- Every metric declares `direction: maximize | minimize`.
- `max_mean_regression_pct` is undefined when the baseline mean is 0; add `max_mean_regression_abs` or the result is INVALID.
- Cases with `status: "error"` are excluded from means and count against `max_execution_errors` (default 0).
- Missing/duplicate cases, missing metrics or checks, and dataset/evaluator mismatches give INVALID (exit 2).

## Development

```bash
uv sync
uv run pytest
uv run evalgate --help
```

## License

MIT
