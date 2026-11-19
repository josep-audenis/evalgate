"""Grade a (fake) support agent with LLM judges, write both runs and calibrate the judge.

The candidate's answers are more helpful on average, but one of them states the wrong refund
window. The `faithful` judge catches it, so the release FAILs.

`fake_complete` stands in for your model client: keyword rules, no network, so this example
runs offline. In real use, `complete` calls your LLM (see docs/judges.md).

Run from the repo root:  python examples/judge/make_runs.py
Then compare:            evalgate compare examples/judge/baseline examples/judge/candidate \
                             --policy examples/judge/evalgate.yaml
And calibrate:           evalgate calibrate examples/judge/candidate \
                             --labels examples/judge/labels.jsonl
"""

import json
import re
from pathlib import Path

from evalgate import EvalRun
from evalgate.evaluators import Rubric, judge
from evalgate.io.labels import calibrate_run

HERE = Path(__file__).parent

DATASET = [
    {
        "id": "refund-window",
        "question": "How long do I have to ask for a refund?",
        "expected": "30 days",
    },
    {
        "id": "refund-method",
        "question": "How is a refund paid?",
        "expected": "original payment method",
    },
    {
        "id": "shipping-time",
        "question": "How long does shipping take?",
        "expected": "5 business days",
    },
    {"id": "shipping-cost", "question": "Is shipping free?", "expected": "orders over 50 EUR"},
    {
        "id": "account-delete",
        "question": "How do I delete my account?",
        "expected": "Settings > Privacy",
    },
    {"id": "account-email", "question": "Can I change my email?", "expected": "Settings > Account"},
]

FAITHFUL = Rubric(
    version="1",
    kind="check",
    template=(
        "Question: $question\nReference: $expected\nAnswer: $output\n"
        "Does the answer agree with the reference? "
        'Reply with only {"pass": true} or {"pass": false}.'
    ),
)
HELPFUL = Rubric(
    version="1",
    kind="score",
    scale=(1, 5),
    template=(
        "Question: $question\nAnswer: $output\n"
        'How helpful is the answer, from 1 to 5? Reply with only {"score": <number>}.'
    ),
)


def fake_complete(prompt: str) -> str:
    """A deterministic stand-in for an LLM judge."""
    answer = re.search(r"^Answer: (.*)$", prompt, re.M)
    assert answer is not None
    reference = re.search(r"^Reference: (.*)$", prompt, re.M)
    if reference:  # the faithfulness rubric
        return json.dumps({"pass": reference.group(1) in answer.group(1)})
    words = len(answer.group(1).split())  # the helpfulness rubric: longer is more helpful
    return json.dumps({"score": max(1, min(5, (words - 8) // 2 + 1)), "reason": "by length"})


def agent(case: dict[str, str], version: str) -> str:
    """The application under test. v2 explains more, but gets one fact wrong."""
    fact = case["expected"]
    if version == "v2" and case["id"] == "refund-window":
        fact = "60 days"
    if version == "v1":
        return f"It is {fact}."
    return f"Good question! The answer is {fact}; reply here if anything is unclear."


EVALUATORS = [
    judge(FAITHFUL, fake_complete, name="faithful", model_id="fake-judge@t0"),
    judge(HELPFUL, fake_complete, name="helpful", model_id="fake-judge@t0"),
]

for run_id, version in (("baseline", "v1"), ("candidate", "v2")):
    with EvalRun(
        run_id,
        dataset="support-faq",
        dataset_version="v1",
        evaluator_version="judges-v1",
        application="support-agent",
        application_version=version,
        evaluators=EVALUATORS,
        path=HERE / run_id,
        overwrite=True,
    ) as run:
        for case in DATASET:
            run.record(case["id"], output=agent(case, version), case=case)

# How far to trust the judges: compare the candidate run with a human's labels. The human
# marked shipping-cost unfaithful (the answer quotes the threshold but not the conditions);
# the judge passed it, which is the one disagreement in calibration.json.
report = calibrate_run(HERE / "candidate", HERE / "labels.jsonl")
(HERE / "calibration.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
