import json
from pathlib import Path
from typing import Any

from evalgate.models import Policy, Run

MANIFEST = {
    "schema_version": "1.0",
    "run_id": "run",
    "dataset_id": "suite",
    "dataset_version": "v1",
    "evaluator_version": "e1",
}

POLICY: dict[str, Any] = {
    "version": 1,
    # Fixtures are deliberately tiny; opt out of the small-sample warning (tested separately).
    "comparison": {"min_compared_cases": 1},
    "metrics": {
        "quality": {
            "path": "scores.quality",
            "direction": "maximize",
            "max_mean_regression_abs": 0.02,
        },
        "latency": {
            "path": "metrics.latency_ms",
            "direction": "minimize",
            "max_mean_regression_pct": 10,
        },
    },
    "checks": {"required": ["safety_valid"], "max_new_failures": 0},
}


def case(case_id: str, quality: float = 0.9, latency: float = 1000, **over: Any) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": "ok",
        "scores": {"quality": quality},
        "checks": {"safety_valid": True, "schema_valid": True},
        "metrics": {"latency_ms": latency},
    } | over


def make_run(cases: list[dict[str, Any]], **manifest: Any) -> Run:
    return Run.model_validate({"manifest": MANIFEST | manifest, "cases": cases})


def make_policy(**over: Any) -> Policy:
    comparison = POLICY["comparison"] | over.pop("comparison", {})
    return Policy.model_validate(POLICY | over | {"comparison": comparison})


def write_run(path: Path, cases: list[dict[str, Any]], **manifest: Any) -> Path:
    path.mkdir(parents=True)
    (path / "run.json").write_text(json.dumps(MANIFEST | manifest))
    (path / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    return path
