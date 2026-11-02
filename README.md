# EvalGate

Local-first AI regression testing and release decision engine.

EvalGate compares a baseline evaluation run against a candidate, finds what improved and what regressed, applies your release policy, and returns an explainable **PASS / FAIL / INVALID** with CI-friendly exit codes (`0` / `1` / `2`). Fully offline, no LLM API key needed.

> Status: early development (Phase 1, core comparison engine). See [docs/SPEC.md](docs/SPEC.md).

## Development

```bash
uv sync
uv run pytest
uv run evalgate --help
```

## License

MIT
