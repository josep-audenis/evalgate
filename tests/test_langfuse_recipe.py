"""Pins the recipe in docs/langfuse.md: its code runs verbatim against stand-ins shaped like
Langfuse SDK 5.2 result objects (checked against the real SDK when the recipe was written)."""

from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest

from evalgate.io import load_run

DOC = Path(__file__).parent.parent / "docs" / "langfuse.md"


def recipe() -> Any:
    namespace: dict[str, Any] = {}
    exec(DOC.read_text(encoding="utf-8").split("```python\n", 1)[1].split("```", 1)[0], namespace)
    return namespace["write_evalgate_run"]


def ev(name: str, value: Any, data_type: str | None = None) -> NS:
    return NS(name=name, value=value, data_type=data_type)


MANIFEST = {"dataset": "d", "dataset_version": "1", "evaluator_version": "e"}


def test_recipe_records_failures_langfuse_only_logs(tmp_path: Path) -> None:
    data = [{"metadata": {"id": i}} for i in ("ok", "task-crash", "judge-crash")]
    result = NS(
        item_results=[
            # Langfuse leaves a failed task's item out; a failed evaluator leaves no evaluations.
            NS(item=data[0], evaluations=[ev("correct", True, "BOOLEAN"), ev("relevance", 0.5)]),
            NS(item=data[2], evaluations=[]),
        ]
    )
    recipe()(
        result,
        data,
        tmp_path / "run",
        run_id="c",
        expected=["correct", "relevance"],
        case_id=lambda item: item["metadata"]["id"],
        **MANIFEST,
    )
    cases = {c.case_id: c for c in load_run(tmp_path / "run").cases}
    assert (cases["ok"].scores, cases["ok"].checks) == ({"relevance": 0.5}, {"correct": True})
    assert cases["task-crash"].status == cases["judge-crash"].status == "error"


def test_recipe_with_hosted_dataset_items(tmp_path: Path) -> None:
    items = [NS(id="item-1"), NS(id="item-2")]
    result = NS(
        item_results=[
            NS(
                item=it,
                evaluations=[ev("score", 0.9, "NUMERIC"), ev("tone", "kind", "CATEGORICAL")],
            )
            for it in items
        ]
    )
    recipe()(
        result,
        items,
        tmp_path / "run",
        run_id="c",
        expected=["score", "tone"],
        case_id=lambda item: item.id,
        **MANIFEST,
    )
    cases = load_run(tmp_path / "run").cases
    assert [(c.case_id, c.scores, c.checks) for c in cases] == [
        ("item-1", {"score": 0.9}, {}),
        ("item-2", {"score": 0.9}, {}),
    ]


# Review findings (PR #20).


def run_recipe(tmp_path: Path, items: list[Any], results: list[NS], expected: list[str]) -> Path:
    recipe()(
        NS(item_results=results),
        items,
        tmp_path / "run",
        run_id="c",
        expected=expected,
        case_id=lambda item: item["metadata"]["id"],
        **MANIFEST,
    )
    return tmp_path / "run"


def test_duplicate_ids_in_data_rejected_before_writing(tmp_path: Path) -> None:
    # Two items with id "a": one succeeded, the other's task failed and was dropped.
    items = [{"metadata": {"id": "a"}}, {"metadata": {"id": "a"}}]
    ok = NS(item=items[0], evaluations=[ev("s", 1.0)])
    with pytest.raises(ValueError, match="unique"):
        run_recipe(tmp_path, items, [ok], ["s"])
    assert not (tmp_path / "run").exists()


def test_duplicate_or_extra_evaluation_names_are_errors(tmp_path: Path) -> None:
    items = [{"metadata": {"id": i}} for i in ("dup", "extra", "ok")]
    results = [
        NS(item=items[0], evaluations=[ev("s", 1.0), ev("s", 0.0)]),  # a judge in between failed
        NS(item=items[1], evaluations=[ev("s", 1.0), ev("new", 1.0)]),
        NS(item=items[2], evaluations=[ev("s", 1.0)]),
    ]
    cases = {c.case_id: c for c in load_run(run_recipe(tmp_path, items, results, ["s"])).cases}
    assert cases["dup"].status == cases["extra"].status == "error"
    assert "new" in (cases["extra"].error or "")
    assert cases["ok"].status == "ok"


def test_boolean_one_zero_and_int_ids(tmp_path: Path) -> None:
    items = [{"metadata": {"id": 7}}, {"metadata": {"id": 8}}]
    results = [
        NS(item=items[0], evaluations=[ev("pass", 1.0, "BOOLEAN")]),
        NS(item=items[1], evaluations=[ev("pass", 0, "BOOLEAN")]),
    ]
    cases = load_run(run_recipe(tmp_path, items, results, ["pass"])).cases
    assert [(c.case_id, c.checks) for c in cases] == [("7", {"pass": True}), ("8", {"pass": False})]


@pytest.mark.parametrize("data_type", ["boolean", "CORRECTION"])
def test_unknown_data_type_stops_without_writing(tmp_path: Path, data_type: str) -> None:
    items = [{"metadata": {"id": "a"}}]
    results = [NS(item=items[0], evaluations=[ev("s", 1.0, data_type)])]
    with pytest.raises(ValueError, match="unknown data_type"):
        run_recipe(tmp_path, items, results, ["s"])
    assert not (tmp_path / "run" / "run.json").exists()
