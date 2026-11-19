"""LLM judges: Rubric, judge(), reply contract, version, isolation (native-evaluators §11, T1)."""

import ast
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import evalgate
from evalgate import EvalRun
from evalgate.evaluators import Rubric, judge

SRC = Path(evalgate.__file__).parent
SECRET = "SENTINEL-41b2-judge"

CHECK = Rubric(version="1", kind="check", template="Q: $question\nA: $output\nReply JSON.")
SCORE = Rubric(version="1", kind="score", scale=(1, 5), template="Rate: $output")


def replying(reply: object) -> Callable[[str], Any]:
    return lambda prompt: reply


def recording(reply: str) -> tuple[Callable[[str], str], list[str]]:
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        return reply

    return complete, prompts


def run_one(
    tmp_path: Path, evaluator: Any, output: Any = "answer", case: Any = None
) -> dict[str, Any]:
    case = {"question": "why?"} if case is None else case
    with EvalRun(
        "r",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=tmp_path,
        evaluators=[evaluator],
    ) as run:
        run.record("c1", output=output, case=case)
    line: dict[str, Any] = json.loads((tmp_path / "cases.jsonl").read_text())
    return line


# --- Rubric construction ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"template": "costs $ 5 $output"},  # bare $
        {"template": "$1 $output"},
        {"template": "no output here"},
        {"scale": (1, 5)},  # scale on a check
        {"version": ""},
        {"kind": "rating"},
    ],
)
def test_bad_check_rubric_is_rejected(kwargs: dict[str, Any]) -> None:
    args: dict[str, Any] = {"version": "1", "kind": "check", "template": "$output"} | kwargs
    with pytest.raises(ValueError):
        Rubric(**args)


@pytest.mark.parametrize(
    "scale",
    [None, (5, 1), (1, 1), (float("nan"), 5), (1, float("inf")), (True, 5), (1, 2, 3), ("1", 5)],
)
def test_bad_score_scale_is_rejected(scale: Any) -> None:
    with pytest.raises(ValueError):
        Rubric(version="1", kind="score", template="$output", scale=scale)


def test_dollar_escape_and_braces_are_fine() -> None:
    Rubric(version="1", kind="check", template='costs $$5: $output -> {"pass": true}')


def test_scale_is_normalized_to_floats() -> None:
    assert Rubric(version="1", kind="score", template="$output", scale=(1, 5)).scale == (1.0, 5.0)


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"complete": "not callable"}, TypeError),
        ({"model_id": ""}, ValueError),
        ({"name": ""}, ValueError),
        ({"rubric": "$output"}, TypeError),
    ],
)
def test_bad_judge_arguments(args: dict[str, Any], error: type[Exception]) -> None:
    kwargs: dict[str, Any] = {
        "rubric": CHECK,
        "complete": replying("{}"),
        "name": "j",
        "model_id": "m",
    } | args
    with pytest.raises(error):
        judge(kwargs.pop("rubric"), kwargs.pop("complete"), **kwargs)


# --- reply contract, through EvalRun ---------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"pass": true}', True),
        ('  {"pass": false, "reason": "wrong"}\n', False),
    ],
)
def test_check_replies(tmp_path: Path, reply: str, expected: bool) -> None:
    line = run_one(tmp_path, judge(CHECK, replying(reply), name="j", model_id="m"))
    assert line["status"] == "ok"
    assert line["checks"] == {"j": expected}


@pytest.mark.parametrize(("reply", "expected"), [("1", 1.0), ("5", 5.0), ("3.5", 3.5)])
def test_score_replies_inclusive_bounds(tmp_path: Path, reply: str, expected: float) -> None:
    evaluator = judge(SCORE, replying(f'{{"score": {reply}}}'), name="j", model_id="m")
    assert run_one(tmp_path, evaluator)["scores"] == {"j": expected}


BAD_CHECK_REPLIES = [
    '```json\n{"pass": true}\n```',
    'Sure! {"pass": true}',
    '{"pass": true, "confidence": 1}',
    '{"score": 3}',
    '{"pass": 1}',
    '{"pass": "true"}',
    '[{"pass": true}]',
    "",
    '{"pass": true, "reason": 3}',
    '{"pass": true, "pass": false}',
    None,
    b'{"pass": true}',
]

BAD_SCORE_REPLIES = [
    '{"score": true}',
    '{"score": NaN}',
    '{"score": 1e999}',
    '{"score": 6}',
    '{"score": 0.99}',
    '{"score": "3"}',
    '{"pass": true}',
    '{"score": ' + "9" * 400 + "}",
]


@pytest.mark.parametrize("reply", BAD_CHECK_REPLIES)
def test_malformed_check_reply_is_an_error(tmp_path: Path, reply: Any) -> None:
    line = run_one(tmp_path, judge(CHECK, replying(reply), name="j", model_id="m"))
    assert line["status"] == "error"
    assert line["error"] == "evaluator j failed"


@pytest.mark.parametrize("reply", BAD_SCORE_REPLIES)
def test_malformed_score_reply_is_an_error(tmp_path: Path, reply: str) -> None:
    line = run_one(tmp_path, judge(SCORE, replying(reply), name="j", model_id="m"))
    assert line["status"] == "error"


def test_complete_raising_is_an_error_without_its_text(tmp_path: Path) -> None:
    def complete(prompt: str) -> str:
        raise RuntimeError(f"{SECRET}: {prompt}")

    line = run_one(tmp_path, judge(CHECK, complete, name="j", model_id="m"), output=SECRET)
    assert line["status"] == "error"
    assert SECRET not in (tmp_path / "cases.jsonl").read_text()


# --- rendering -------------------------------------------------------------------------------


def test_missing_placeholder_is_an_error(tmp_path: Path) -> None:
    line = run_one(
        tmp_path, judge(CHECK, replying('{"pass": true}'), name="j", model_id="m"), case={}
    )
    assert line["status"] == "error"


def test_only_used_fields_are_rendered(tmp_path: Path) -> None:
    complete, prompts = recording('{"pass": true}')
    case = {"question": "why?", "unused": datetime(2026, 1, 1)}
    line = run_one(tmp_path, judge(CHECK, complete, name="j", model_id="m"), case=case)
    assert line["status"] == "ok"
    assert prompts == ["Q: why?\nA: answer\nReply JSON."]


def test_unserializable_used_field_is_an_error(tmp_path: Path) -> None:
    case = {"question": datetime(2026, 1, 1)}
    evaluator = judge(CHECK, replying('{"pass": true}'), name="j", model_id="m")
    assert run_one(tmp_path, evaluator, case=case)["status"] == "error"


def test_substitution_is_single_pass_and_output_wins(tmp_path: Path) -> None:
    complete, prompts = recording('{"pass": true}')
    case = {"question": "$output", "output": "not this"}
    run_one(tmp_path, judge(CHECK, complete, name="j", model_id="m"), output="$question", case=case)
    assert prompts == ["Q: $output\nA: $question\nReply JSON."]


def test_non_string_values_are_canonical_json(tmp_path: Path) -> None:
    complete, prompts = recording('{"pass": true}')
    output = {"b": 1, "a": "é"}
    run_one(tmp_path, judge(CHECK, complete, name="j", model_id="m"), output=output)
    assert prompts[0].splitlines()[1] == 'A: {"a":"é","b":1}'


# --- version ---------------------------------------------------------------------------------


def version(rubric: Rubric = CHECK, model_id: str = "m") -> str:
    return judge(rubric, replying(""), name="j", model_id=model_id).version


def test_version_is_readable_and_stable() -> None:
    assert version().startswith("1+m+")
    assert version() == version()
    assert judge(CHECK, replying(""), name="other", model_id="m").version == version()


@pytest.mark.parametrize(
    "changed",
    [
        lambda: version(Rubric(version="2", kind="check", template=CHECK.template)),
        lambda: version(Rubric(version="1", kind="check", template=CHECK.template + " ")),
        lambda: version(model_id="m2"),
        lambda: version(Rubric(version="1", kind="score", template=CHECK.template, scale=(1, 5))),
    ],
)
def test_version_changes(changed: Callable[[], str]) -> None:
    assert changed() != version()


def test_scale_change_changes_version_but_int_float_does_not() -> None:
    def of(scale: tuple[float, float]) -> str:
        return version(Rubric(version="1", kind="score", template="$output", scale=scale))

    assert of((1, 5)) == of((1.0, 5.0))
    assert of((1, 5)) != of((1, 10))


def test_plus_in_parts_cannot_collide() -> None:
    a = version(Rubric(version="1+a", kind="check", template="$output"), model_id="b")
    b = version(Rubric(version="1", kind="check", template="$output"), model_id="a+b")
    assert a != b


# --- isolation -------------------------------------------------------------------------------

ENGINE = [
    SRC / "validation.py",
    SRC / "engine.py",
    SRC / "api.py",
    *(SRC / "comparison").glob("*.py"),
    *(SRC / "policies").glob("*.py"),
]
JUDGE_IMPORTS = {
    "collections.abc",
    "dataclasses",
    "hashlib",
    "json",
    "math",
    "os",
    "pathlib",
    "string",
    "tempfile",
    "typing",
    "evalgate._json",
    "evalgate.evaluators.core",
}


def imported(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


@pytest.mark.parametrize("path", ENGINE, ids=lambda p: p.name)
def test_engine_never_references_the_judge(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "evaluators.judge" not in text
    assert not any(m.startswith("evalgate.evaluators") for m in imported(path))


def test_judge_imports_only_allowlisted_modules() -> None:
    assert imported(SRC / "evaluators" / "judge.py") <= JUDGE_IMPORTS


# --- shared strict JSON ----------------------------------------------------------------------


@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', "NaN", "[Infinity]", "1e999"])
def test_loads_strict_rejects(text: str) -> None:
    from evalgate._json import loads_strict

    with pytest.raises(ValueError) as exc:
        loads_strict(text)
    fixed = {"duplicate key", "number out of range"}
    assert str(exc.value) in fixed or str(exc.value).startswith("non-standard JSON constant")


def test_versioned_builtins_keep_their_0_7_behaviour() -> None:
    from evalgate.evaluators import json_valid

    assert json_valid(name="j")("1e999", {}) is True  # finite=False: unchanged since 0.7
