"""Evaluator core (native-evaluators design §1): interface, typing, fail closed."""

from decimal import Decimal
from typing import Any

import pytest

from evalgate.evaluators import Applied, Evaluator, apply, check, score


@check(name="has_hi", version="1")
def has_hi(output: Any, case: Any) -> bool:
    return "hi" in output


@score(name="length", version="1")
def length(output: Any, case: Any) -> float:
    return len(output)


def const(kind: str, value: object, name: str = "e") -> Evaluator:
    return Evaluator(name, "1", kind, lambda o, c: value)  # type: ignore[arg-type]


def test_decorators_build_evaluators() -> None:
    assert (has_hi.name, has_hi.version, has_hi.kind) == ("has_hi", "1", "check")
    assert (length.name, length.kind) == ("length", "score")


def test_apply_routes_results_by_kind() -> None:
    assert apply([has_hi, length], "hi there", {}) == Applied({"length": 8.0}, {"has_hi": True})
    assert apply([has_hi], "nope", {}) == Applied({}, {"has_hi": False})
    assert apply([], "x", {}) == Applied({}, {})


@pytest.mark.parametrize(
    "kind,value",
    [
        ("check", 1),
        ("check", 0.0),
        ("check", None),
        ("check", "true"),
        ("score", True),
        ("score", None),
        ("score", "0.5"),
        ("score", float("nan")),
        ("score", float("inf")),
        ("score", float("-inf")),
        ("score", 10**400),
        ("score", Decimal("1")),
    ],
)
def test_wrong_type_is_an_evaluator_error_never_coerced(kind: str, value: object) -> None:
    assert apply([const(kind, value)], "x", {}) == Applied({}, {}, "e")


def test_int_score_is_a_float() -> None:
    result = apply([const("score", 3)], "x", {})
    assert result.scores == {"e": 3.0} and type(result.scores["e"]) is float


class FakeNumpyBool:
    """Duck-types a numpy bool scalar, so the unwrap path runs without numpy installed."""

    class dtype:  # noqa: N801
        kind = "b"

    shape = ()

    def __init__(self, value: bool) -> None:
        self.value = value

    def __bool__(self) -> bool:
        return self.value


def test_numpy_bool_check_is_accepted() -> None:
    assert apply([const("check", FakeNumpyBool(False))], "x", {}).checks == {"e": False}


def test_real_numpy_values() -> None:
    np = pytest.importorskip("numpy")
    assert apply([const("check", np.bool_(True))], "x", {}).checks == {"e": True}
    assert apply([const("score", np.float64(0.5))], "x", {}).scores == {"e": 0.5}


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        apply([const("check", False, "d"), const("check", True, "d")], "x", {})


def test_decorated_evaluator_is_callable() -> None:
    assert has_hi("hi", {}) is True


def test_exception_fails_closed_without_its_text() -> None:
    @check(name="boom", version="1")
    def boom(output: Any, case: Any) -> bool:
        raise ValueError(f"secret output: {output}")

    result = apply([has_hi, boom, length], "SECRET", {})
    assert result == Applied({}, {}, "boom")
    assert "SECRET" not in repr(result)


def test_first_failure_is_reported_deterministically() -> None:
    assert apply([const("check", None, "a"), const("check", None, "b")], "x", {}).failed == "a"


def test_base_exception_is_not_caught() -> None:
    @check(name="stop", version="1")
    def stop(output: Any, case: Any) -> bool:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        apply([stop], "x", {})


def test_case_is_read_only_and_not_mutated() -> None:
    @check(name="mutate", version="1")
    def mutate(output: Any, case: Any) -> bool:
        case["expected"] = "changed"
        return True

    record = {"expected": "orig"}
    assert apply([mutate], "x", record).failed == "mutate"
    assert record == {"expected": "orig"}


def test_nested_case_values_are_isolated() -> None:
    @check(name="append", version="1")
    def append(output: Any, case: Any) -> bool:
        case["items"].append(1)
        return True

    @check(name="unchanged", version="1")
    def unchanged(output: Any, case: Any) -> bool:
        return bool(case["items"] == [])

    record: dict[str, Any] = {"items": []}
    assert apply([append, unchanged], "x", record).checks == {"append": True, "unchanged": True}
    assert record == {"items": []}


def test_evaluator_receives_case_fields() -> None:
    @check(name="eq", version="1")
    def eq(output: Any, case: Any) -> bool:
        return bool(output == case["expected"])

    assert apply([eq], "a", {"expected": "a"}).checks == {"eq": True}


@pytest.mark.parametrize(
    "args",
    [("", "1", "check"), ("n", "", "check"), ("n", "1", "bogus"), (None, "1", "check")],
)
def test_invalid_evaluator_definition_raises(args: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        Evaluator(*args, lambda o, c: True)


def test_non_callable_fn_raises() -> None:
    with pytest.raises(TypeError):
        Evaluator("n", "1", "check", "not callable")  # type: ignore[arg-type]
