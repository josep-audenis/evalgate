"""Pins the recipe in docs/langfuse.md: its code runs verbatim against stand-ins shaped like
Langfuse SDK 5.2 result objects (checked against the real SDK when the recipe was written)."""

from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

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
