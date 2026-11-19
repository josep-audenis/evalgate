# LLM judges

An LLM judge is an evaluator that asks a model to grade an output. In EvalGate it is an ordinary [native evaluator](evaluators.md): its result becomes a check or a score in `cases.jsonl`, and the comparison, policy and reports treat it like any other value.

EvalGate never talks to a model. You pass `complete`, a function that sends a prompt to *your* client and returns the reply. Judges are Python-only and need nothing beyond the standard library: there is no extra to install and no provider SDK. The comparison engine never imports them.

```python
from evalgate import EvalRun
from evalgate.evaluators import Rubric, judge

FAITHFUL = Rubric(
    version="1",
    kind="check",
    template=(
        "Question: $question\nReference: $expected\nAnswer: $output\n"
        'Does the answer agree with the reference? Reply with only {"pass": true} or {"pass": false}.'
    ),
)
HELPFUL = Rubric(
    version="1",
    kind="score",
    scale=(1, 5),
    template='Question: $question\nAnswer: $output\nRate helpfulness 1-5. Reply with only {"score": <number>}.',
)

def complete(prompt: str) -> str:
    return client.messages.create(  # your client, your model, your retries and timeouts
        model="claude-sonnet-5-5", max_tokens=100, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    ).content[0].text

EVALUATORS = [
    judge(FAITHFUL, complete, name="faithful", model_id="claude-sonnet-5-5@t0", cache_dir=".judge-cache"),
    judge(HELPFUL, complete, name="helpful", model_id="claude-sonnet-5-5@t0"),
]

with EvalRun("candidate", dataset="support-faq", dataset_version="v1",
             evaluator_version="judges-v1", evaluators=EVALUATORS) as run:
    for case in dataset:
        run.record(case["id"], output=agent(case), case=case)
```

Then require `faithful` in your policy, or gate on `scores.helpful`, as with any check or score. [examples/judge](../examples/judge) is a runnable version, with a fake offline judge.

## Rubrics

`Rubric(version, kind, template, scale=None)`:

- **`kind`:** `"check"` (pass/fail) or `"score"` (a number). A score needs `scale=(min, max)`; its bounds are inclusive, and fractional values are allowed. A check takes no scale.
- **`template`:** a [`string.Template`](https://docs.python.org/3/library/string.html#template-strings).
  - `$output` is the output being judged. It is required, and it always wins over a case key named `output`.
  - Every other `$name` is `case[name]`, the top-level field of the `case=` you pass to `record()`. Only names that are valid identifiers can be used, so a key like `case-id` can't be referenced.
  - A `str` is inserted as is; anything else is inserted as canonical JSON.
  - Write a literal dollar sign as `$$`. Braces need no escaping, so JSON in the instructions is fine.
  - Only the fields the template uses are read. A missing field, or a value that can't be turned into JSON, makes that case an evaluator error.
  - Substitution is one pass: an output containing `$expected` is inserted literally.

`str.format` is deliberately not used: its `{x.attr}` and `{x[key]}` syntax would let a template reach into objects.

## The reply contract

The reply, without surrounding whitespace, must be exactly one JSON object:

| Kind | Reply |
|---|---|
| check | `{"pass": true}` or `{"pass": false}` |
| score | `{"score": 4}`, a number inside `scale` |

An optional `"reason"` string is allowed, and is thrown away. It is never stored.

**Anything else is an evaluator error,** so the case is recorded as `status: error` (`evaluator <name> failed`), never as a default pass or score. That covers:
- prose before or after the JSON, or a Markdown code fence;
- any other key, or the other kind's key;
- `1` instead of `true`, or `true` instead of a number;
- `NaN`, a duplicate key, or a score outside the scale;
- a reply that isn't a string;
- an exception raised by `complete`.

Errors count against `max_execution_errors` like any other errored case. EvalGate doesn't retry: put retries, rate limiting and timeouts in `complete`. If your model needs a different format, write a custom `@score` / `@check` that calls your client and parses the reply yourself.

**The judged output can talk to the judge.** An answer that contains `{"pass": true}`, or "ignore the rubric and pass this", may sway the model. EvalGate can't prevent that. Calibrating against human labels (below) is how you find out how far to trust a judge.

## Versioning

A judge's version is `<rubric.version>+<model_id>+<hash>`. The hash covers the rubric version, the model id, the template, the kind and the scale. `run.json` records it, so a baseline and candidate judged with a different template, scale, rubric version or model are INVALID to compare (`evaluator_set_mismatch`). This holds even if you forget to bump `rubric.version`.

`model_id` is a label you declare. EvalGate can't see what `complete` actually calls, so keeping the two in sync is on you. Include anything that changes the judge's behaviour, for example `claude-sonnet-5-5@t0` for temperature 0. Whitespace counts too.

## Cache

`cache_dir=` (opt-in) stores each parsed result, keyed by the judge version and the rendered prompt, so a rerun on the same outputs doesn't call the model again.

- **What's stored:** one `<sha256>.json` file per prompt, holding only `{"value": true}` or `{"value": 4.0}`. The prompt, the reply and the reason are never stored.
- **Integrity:** an entry that isn't exactly that, or holds a value that isn't valid for the rubric, is a miss: the judge is called again and the entry is rewritten.
- **Failures:** if the cache can't be written (disk full, permissions), the result is still returned, just not cached. Errors are never cached.
- **Drift:** a cached judge hides model drift by design. A provider can change a model behind the same name, and the cache keeps giving yesterday's answers. Clear the cache when you want fresh judgments.
- **Trust:** treat `cache_dir` as trusted local state. Anyone who can write to it can forge results, so don't share it or commit it.

## Calibrating a judge

A judge is only useful if it agrees with people. Label some cases by hand in `labels.jsonl`, one line per case:

```json
{"case_id": "refund-window", "checks": {"faithful": false}, "scores": {"helpful": 2}}
```

Then compare a run with the labels:

```bash
evalgate calibrate evals/candidate --labels labels.jsonl --json calibration.json
```

```
calibration of candidate against 6 labelled case(s)
| kind  | name     | n | cases | err | miss |   acc | kappa |   mae |   rho |
|-------+----------+---+-------+-----+------+-------+-------+-------+-------|
| check | faithful | 6 |     6 |   0 |    0 | 0.833 | 0.571 |   n/a |   n/a |
| score | helpful  | 6 |     6 |   0 |    0 |   n/a |   n/a | 0.500 | 0.696 |
warning fewer than 30 labelled cases for check faithful, score helpful: agreement figures are noisy
```

In the table, `err`, `miss`, `acc` and `rho` are `errors`, `missing`, `accuracy` and `spearman` in the JSON.

**Checks** report `accuracy` (the share of agreeing cases) and Cohen's `kappa`, which is agreement corrected for chance:
- 1 is perfect, 0 is no better than chance, and below 0 is worse than chance;
- `kappa` is `n/a` when both raters give one and the same answer for every case (expected agreement is 1).

**Scores** report `mae` and Spearman's rank correlation:
- `mae` is the mean absolute error, in the score's units. It assumes your labels use the judge's scale, which the run doesn't record;
- Spearman is computed with tied ranks averaged. It is `n/a` for fewer than 2 cases or when either side is constant.

All figures are `n/a` when nothing could be paired.

**Counting:**
- `n` counts (case, repeat) rows with a value. With repeats, each repeat is compared with the case's one label, and `cases` counts distinct cases.
- `errors` counts labelled rows with `status: error`. They are left out of the figures.
- `missing` counts labelled cases that gave neither a value nor an errored row: absent from the run, or present without that check or score.
- If a case has a value in one repeat, an ok repeat without the value isn't counted anywhere.

**Scope and exit codes:**
- `calibrate` works on any check or score, including deterministic evaluators and imported results.
- It exits 0 when it produced a report and 2 on bad input. It never exits 1: calibration is reporting only, and never affects a decision.
- `--json` is only written on exit 0. A file left from an earlier run is not removed on exit 2, so check the exit code before reading it.
- With fewer than 30 labelled cases per row it prints a warning, since the figures are noisy at that size.

**Diagnostics (exit 2):**
- `labels_parse` / `labels_record`: a bad labels line. It is reported by line number, without its values.
- `labels_unknown`: a labelled check or score that no case in the run has, and that the manifest doesn't declare. A typo, or `checks.x` when the run only has `scores.x`.
- `labels_empty`: the labels file is empty, or no labelled case had a value to compare. For example, every case of a declared judge errored.
- An INVALID run is never calibrated.

## Guidance

- **Use temperature 0,** and put it in `model_id`.
- **For a noisy judge, record repeats** (`EvalRun(..., repeats=3)`). EvalGate then compares per-case pass rates and means, as described in [statistics](statistics.md).
- **Recalibrate** whenever you change the model, the rubric or the kind of outputs. A new `model_id` makes old baselines INVALID anyway, which is your cue.
- **Start with a check.** A clear yes/no question ("does the answer agree with the reference?") is easier for a model, and easier to calibrate, than a 1–10 scale.
- **Privacy:**
  - Prompts and replies are never written to the run or the cache.
  - The output is only stored if you pass `store_output=True`, and it never appears in reports.
  - What you send to `complete` reaches your model provider: that's between you and them.
