"""Score a (fake) support agent with EvalGate's built-in evaluators and write both runs.

The candidate answers better on average, but one answer comes back wrapped in a Markdown code
fence, so it is no longer valid JSON. EvalGate's `is_json` check catches it.

Run from the repo root:  python examples/native-evaluators/make_runs.py
Then compare:            evalgate compare examples/native-evaluators/baseline \
                             examples/native-evaluators/candidate \
                             --policy examples/native-evaluators/evalgate.yaml
"""

import json
from pathlib import Path

from pydantic import BaseModel

from evalgate import EvalRun
from evalgate.evaluators import json_valid, length, matches_model, not_contains

HERE = Path(__file__).parent

DATASET = [
    {"id": "refund-001", "category": "billing"},
    {"id": "refund-002", "category": "billing"},
    {"id": "account-001", "category": "account"},
    {"id": "account-002", "category": "account"},
    {"id": "shipping-001", "category": "shipping"},
]


class Answer(BaseModel):
    answer: str
    confidence: float


EVALUATORS = [
    json_valid(name="is_json"),
    matches_model(Answer, name="answer_shape", version="1"),
    length(name="concise", max=300),
    not_contains("as an AI", name="no_ai_disclaimer", ignore_case=True),
]


def agent(case_id: str, version: str) -> tuple[str, float]:
    """A stand-in for the application under test: returns (output, quality score)."""
    answer = json.dumps({"answer": f"Here is how to resolve {case_id}.", "confidence": 0.8})
    if version == "v2" and case_id == "account-002":
        return f"```json\n{answer}\n```", 0.97  # fenced: not JSON any more
    return answer, 0.86 if version == "v2" else 0.80


for run_id, version in (("baseline", "v1"), ("candidate", "v2")):
    with EvalRun(
        run_id,
        dataset="support-suite",
        dataset_version="v3",
        evaluator_version="native-v1",
        application="support-agent",
        application_version=version,
        evaluators=EVALUATORS,
        path=HERE / run_id,
        overwrite=True,
    ) as run:
        for case in DATASET:
            output, quality = agent(case["id"], version)
            run.record(
                case["id"],
                output=output,
                case=case,
                scores={"quality": quality},  # e.g. from your own grader
                metadata={"category": case["category"]},
            )
