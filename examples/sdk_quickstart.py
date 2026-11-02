"""Record two runs from a (fake) eval loop and compare them.

Run from the repo root: python examples/sdk_quickstart.py
"""

import sys
import time

from evalgate import EvalRun, compare

DATASET = [
    {"id": "refund-001", "category": "billing"},
    {"id": "account-001", "category": "account"},
    {"id": "abuse-001", "category": "safety"},
]


def execute_agent(case: dict[str, str], version: str) -> str:
    if version == "v2" and case["id"] == "account-001":
        raise TimeoutError("upstream API timed out")
    return f"answer to {case['id']}"


for version in ("v1", "v2"):
    run_id = "baseline" if version == "v1" else "candidate"
    with EvalRun(
        run_id,
        dataset="support-suite",
        dataset_version="v3",
        evaluator_version="rubric-v2",
        application_version=version,
        overwrite=True,  # this demo rewrites evals/ on every run
    ) as run:
        for case in DATASET:
            start = time.perf_counter()
            try:
                output = execute_agent(case, version)
            except Exception as exc:
                run.record_error(case["id"], repr(exc), metadata={"category": case["category"]})
                continue
            run.record(
                case["id"],
                scores={"quality": 0.9 if version == "v2" else 0.8},
                checks={"safety_valid": "answer" in output},
                metrics={"latency_ms": (time.perf_counter() - start) * 1000},
                metadata={"category": case["category"]},
            )

report = compare("evals/baseline", "evals/candidate", policy="examples/sdk_quickstart.yaml")
print(report.summary())
report.to_json("evals/report.json")
sys.exit({"PASS": 0, "FAIL": 1, "INVALID": 2}[report.status])
