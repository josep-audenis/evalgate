"""Write one baseline and three candidates for `evalgate compare BASELINE CANDIDATE...`.

- candidate-a: the best quality, but one answer is no longer safe -> FAIL
- candidate-b: better quality, safe -> PASS
- candidate-c: slightly better quality, safe -> PASS

With `--rank-by quality`, candidate-a ranks first while marked FAIL: the ranking says which is
best on one metric, never which may ship.

Run from the repo root:  python examples/multi-candidate/make_runs.py
Then compare:            evalgate compare examples/multi-candidate/baseline \
                             examples/multi-candidate/candidate-a \
                             examples/multi-candidate/candidate-b \
                             examples/multi-candidate/candidate-c \
                             --policy examples/multi-candidate/evalgate.yaml --rank-by quality
"""

from pathlib import Path

from evalgate import EvalRun

HERE = Path(__file__).parent
CASES = ["refund-001", "refund-002", "account-001", "account-002", "shipping-001", "shipping-002"]
BASE_QUALITY = [0.80, 0.78, 0.82, 0.79, 0.81, 0.80]

RUNS = {
    # run dir: (application_version, quality lift, case that becomes unsafe)
    "baseline": ("v2.0.0", 0.00, None),
    "candidate-a": ("v2.1.0-a", 0.08, "account-002"),
    "candidate-b": ("v2.1.0-b", 0.05, None),
    "candidate-c": ("v2.1.0-c", 0.02, None),
}

for name, (version, lift, unsafe) in RUNS.items():
    with EvalRun(
        name,
        dataset="support-suite",
        dataset_version="v5",
        evaluator_version="rubric-v3",
        application="support-agent",
        application_version=version,
        path=HERE / name,
        overwrite=True,
    ) as run:
        for case_id, quality in zip(CASES, BASE_QUALITY, strict=True):
            run.record(
                case_id,
                scores={"quality": round(quality + lift, 4)},
                checks={"safety_valid": case_id != unsafe},
                metrics={"latency_ms": 2000},
            )
