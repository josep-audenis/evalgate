"""Judge result cache (native-evaluators §11, T2)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evalgate import EvalRun
from evalgate.evaluators import Evaluator, Rubric, judge

CHECK = Rubric(version="1", kind="check", template="Q: $question A: $output")
SCORE = Rubric(version="1", kind="score", scale=(1, 5), template="Rate: $output")
SECRET_OUT = "SENTINEL-out-5a1"
SECRET_REASON = "SENTINEL-reason-5a1"


class Counter:
    def __init__(self, reply: str = '{"pass": true}') -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        return self.reply


def results(path: Path, evaluator: Evaluator, outputs: list[Any], question: str = "q") -> list[Any]:
    with EvalRun(
        "r",
        dataset="d",
        dataset_version="v",
        evaluator_version="e",
        path=path,
        evaluators=[evaluator],
        overwrite=True,
    ) as run:
        for i, output in enumerate(outputs):
            run.record(f"c{i}", output=output, case={"question": question})
    lines = [json.loads(x) for x in (path / "cases.jsonl").read_text().splitlines()]
    return [line.get("checks") or line.get("scores") or line["status"] for line in lines]


def entries(cache: Path) -> list[Path]:
    return sorted(cache.glob("*.json"))


def test_hit_skips_complete_and_matches_fresh_call(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    first = Counter('{"score": 4, "reason": "fine"}')
    fresh = results(
        tmp_path / "a", judge(SCORE, first, name="j", model_id="m", cache_dir=cache), ["x", "y"]
    )
    assert first.calls == 2 and len(entries(cache)) == 2

    def boom(prompt: str) -> str:
        raise AssertionError("called on a cache hit")

    second = judge(SCORE, boom, name="j", model_id="m", cache_dir=cache)
    assert results(tmp_path / "b", second, ["x", "y"]) == fresh == [{"j": 4.0}, {"j": 4.0}]


def test_check_false_is_a_hit(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    results(
        tmp_path / "a",
        judge(CHECK, Counter('{"pass": false}'), name="j", model_id="m", cache_dir=cache),
        ["x"],
    )
    counter = Counter()
    assert results(
        tmp_path / "b", judge(CHECK, counter, name="j", model_id="m", cache_dir=cache), ["x"]
    ) == [{"j": False}]
    assert counter.calls == 0


@pytest.mark.parametrize(
    "variant",
    [
        lambda c: judge(
            Rubric(version="2", kind="check", template=CHECK.template),
            Counter(),
            name="j",
            model_id="m",
            cache_dir=c,
        ),
        lambda c: judge(
            Rubric(version="1", kind="check", template=CHECK.template + "!"),
            Counter(),
            name="j",
            model_id="m",
            cache_dir=c,
        ),
        lambda c: judge(CHECK, Counter(), name="j", model_id="m2", cache_dir=c),
    ],
)
def test_changed_judge_is_a_miss(tmp_path: Path, variant: Callable[[Path], Evaluator]) -> None:
    cache = tmp_path / "cache"
    results(tmp_path / "a", judge(CHECK, Counter(), name="j", model_id="m", cache_dir=cache), ["x"])
    evaluator = variant(cache)
    results(tmp_path / "b", evaluator, ["x"])
    assert len(entries(cache)) == 2


def test_changed_prompt_input_is_a_miss(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    results(
        tmp_path / "a",
        judge(CHECK, Counter(), name="j", model_id="m", cache_dir=cache),
        ["x"],
        question="q1",
    )
    counter = Counter()
    results(
        tmp_path / "b",
        judge(CHECK, counter, name="j", model_id="m", cache_dir=cache),
        ["x"],
        question="q2",
    )
    assert counter.calls == 1


@pytest.mark.parametrize(
    ("rubric", "content"),
    [
        (CHECK, "not json"),
        (CHECK, '{"value": true, "value": false}'),
        (CHECK, '{"value": true, "extra": 1}'),
        (CHECK, '{"value": 1}'),
        (CHECK, "[true]"),
        (CHECK, "{}"),
        (SCORE, '{"value": NaN}'),
        (SCORE, '{"value": 1e999}'),
        (SCORE, '{"value": true}'),
        (SCORE, '{"value": 9}'),
        (SCORE, '{"value": "3"}'),
    ],
)
def test_malformed_entry_is_a_miss_and_is_rewritten(
    tmp_path: Path, rubric: Rubric, content: str
) -> None:
    cache = tmp_path / "cache"
    reply = '{"pass": true}' if rubric.kind == "check" else '{"score": 2}'
    results(
        tmp_path / "a",
        judge(rubric, Counter(reply), name="j", model_id="m", cache_dir=cache),
        ["x"],
    )
    [entry] = entries(cache)
    entry.write_text(content, encoding="utf-8")
    counter = Counter(reply)
    results(tmp_path / "b", judge(rubric, counter, name="j", model_id="m", cache_dir=cache), ["x"])
    assert counter.calls == 1
    assert json.loads(entry.read_text()) == {"value": True if rubric.kind == "check" else 2.0}


def test_unreadable_entry_is_a_miss(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    results(tmp_path / "a", judge(CHECK, Counter(), name="j", model_id="m", cache_dir=cache), ["x"])
    [entry] = entries(cache)
    entry.write_bytes(b"\xff\xfe\x00")
    counter = Counter()
    assert results(
        tmp_path / "b", judge(CHECK, counter, name="j", model_id="m", cache_dir=cache), ["x"]
    ) == [{"j": True}]
    assert counter.calls == 1


def test_errors_are_not_cached(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    assert results(
        tmp_path / "a",
        judge(CHECK, Counter("nope"), name="j", model_id="m", cache_dir=cache),
        ["x"],
    ) == ["error"]
    assert not cache.exists() or entries(cache) == []


def test_write_failure_still_returns_the_value(tmp_path: Path) -> None:
    cache = tmp_path / "cache-is-a-file"
    cache.write_text("x")
    evaluator = judge(CHECK, Counter(), name="j", model_id="m", cache_dir=cache)
    assert results(tmp_path / "a", evaluator, ["x"]) == [{"j": True}]


def test_failed_replace_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    judge_module = sys.modules["evalgate.evaluators.judge"]  # the package exports judge()

    real = judge_module.os.replace

    def fail(src: Any, dst: Any) -> None:  # only the cache's rename fails, not EvalRun's
        if "cache" in str(dst):
            raise PermissionError("locked")
        real(src, dst)

    monkeypatch.setattr(judge_module.os, "replace", fail)
    cache = tmp_path / "cache"
    evaluator = judge(CHECK, Counter(), name="j", model_id="m", cache_dir=cache)
    assert results(tmp_path / "a", evaluator, ["x"]) == [{"j": True}]
    assert list(cache.iterdir()) == []


def test_cache_holds_values_only_and_no_temp_files(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    reply = f'{{"score": 3, "reason": "{SECRET_REASON}"}}'
    results(
        tmp_path / "a",
        judge(SCORE, Counter(reply), name="j", model_id="m", cache_dir=cache),
        [SECRET_OUT],
    )
    files = list(cache.iterdir())
    assert [f.suffix for f in files] == [".json"]
    text = files[0].read_text()
    assert text == '{"value":3.0}'
    assert SECRET_OUT not in text and SECRET_REASON not in text and "Rate" not in text


def test_bad_cache_dir_type() -> None:
    with pytest.raises(TypeError):
        judge(CHECK, Counter(), name="j", model_id="m", cache_dir=42)  # type: ignore[arg-type]
