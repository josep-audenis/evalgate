"""Built-in evaluators (native-evaluators design §2): pass/fail, errors, argument versioning."""

import builtins
import re
from typing import Any

import pytest
from pydantic import BaseModel, field_validator

from evalgate.evaluators import (
    Evaluator,
    apply,
    contains,
    exact_match,
    json_schema,
    json_valid,
    length,
    matches_model,
    not_contains,
    numeric_close,
    regex,
)

VERSION = re.compile(r"^1\+[0-9a-f]{12}$")


class Answer(BaseModel):
    answer: str
    confidence: float


def run(ev: Evaluator, output: Any, case: dict[str, Any] | None = None) -> bool | str:
    """True/False for a check result, or "error" when the evaluator failed."""
    result = apply([ev], output, case or {})
    return "error" if result.failed else result.checks[ev.name]


# --- pass / fail -----------------------------------------------------------------------------

CASES: list[tuple[Evaluator, Any, Any, dict[str, Any]]] = [
    # evaluator, passing output, failing output, case
    (json_valid(name="j"), '{"a": [1, 2]}', '{"a": 1,}', {}),
    (json_valid(name="j"), "[]", "NaN", {}),
    (json_valid(name="j"), '"x"', '{"a": 1, "a": 2}', {}),
    (json_valid(name="j"), "null", {"a": 1}, {}),  # a dict is not JSON text
    (matches_model(Answer, name="m", version="1"), '{"answer":"x","confidence":0.5}', "{}", {}),
    (matches_model(Answer, name="m", version="1"), {"answer": "x", "confidence": 1}, "nope", {}),
    (regex(r"\d{3}", name="r"), "call 555 now", "call now", {}),
    (regex(r"sorry", name="r", must_match=False), "sure", "sorry, no", {}),
    (regex(r"x", name="r"), "x", 5, {}),
    (contains("Refund", name="c"), "our Refund policy", "our refund policy", {}),
    (contains("Refund", name="c", ignore_case=True), "our REFUND policy", "nothing", {}),
    (not_contains("as an AI", name="n"), "hello", "as an AI model", {}),
    (exact_match(name="e"), "Paris", "paris", {"expected": "Paris"}),
    (
        exact_match(name="e", normalize=["strip", "casefold"]),
        " PARIS ",
        "Lyon",
        {"expected": "paris"},
    ),
    (exact_match(name="e", field="gold"), 3, "3", {"gold": 3}),
    (length(name="l", max=5), "short", "too long", {}),
    (length(name="l", min=2, unit="words"), "two words", "one", {}),
    (length(name="l", min=1), "x", 123, {}),
    (numeric_close(name="n", abs_tol=0.01), "3.141", "3.2", {"expected": 3.14159}),
    (numeric_close(name="n", rel_tol=0.1), 105, 120, {"expected": 100}),
    (numeric_close(name="n"), " 2 ", "two", {"expected": 2}),
    (numeric_close(name="n", abs_tol=1), 1.0, "nan", {"expected": 1}),
    (numeric_close(name="n", abs_tol=1), 1.0, True, {"expected": 1}),
]


@pytest.mark.parametrize("ev,good,bad,case", CASES)
def test_pass_and_fail(ev: Evaluator, good: Any, bad: Any, case: dict[str, Any]) -> None:
    assert run(ev, good, case) is True
    assert run(ev, bad, case) is False


def test_json_schema_pass_and_fail() -> None:
    pytest.importorskip("jsonschema")
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}
    ev = json_schema(schema, name="s")
    assert run(ev, '{"a": 1}') is True
    assert run(ev, '{"a": "1"}') is False
    assert run(ev, "not json") is False
    assert run(ev, '{"a": NaN}') is False


# --- evaluator errors (misconfiguration), never a pass ------------------------------------------


@pytest.mark.parametrize(
    "ev,case",
    [
        (exact_match(name="e"), {}),
        (numeric_close(name="n"), {}),
        (numeric_close(name="n"), {"expected": "3"}),
        (numeric_close(name="n"), {"expected": None}),
        (numeric_close(name="n"), {"expected": True}),
        (numeric_close(name="n"), {"expected": float("nan")}),
    ],
)
def test_missing_or_bad_case_field_is_an_evaluator_error(
    ev: Evaluator, case: dict[str, Any]
) -> None:
    assert run(ev, "3", case) == "error"


class Strict(BaseModel):
    n: int

    @field_validator("n")
    @classmethod
    def positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v


def test_matches_model_runs_validators() -> None:
    ev = matches_model(Strict, name="m", version="1")
    assert run(ev, '{"n": 1}') is True
    assert run(ev, '{"n": -1}') is False


# --- construction errors -------------------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [
        lambda: regex("(", name="r"),
        lambda: regex(1, name="r"),  # type: ignore[arg-type]
        lambda: contains("", name="c"),
        lambda: not_contains("", name="c"),
        lambda: exact_match(name="e", normalize=["lower"]),
        lambda: exact_match(name="e", normalize="strip"),
        lambda: length(name="l"),
        lambda: length(name="l", min=-1),
        lambda: length(name="l", min=5, max=2),
        lambda: length(name="l", max=True),
        lambda: length(name="l", max=2, unit="bytes"),  # type: ignore[arg-type]
        lambda: numeric_close(name="n", abs_tol=-1),
        lambda: numeric_close(name="n", rel_tol=float("nan")),
        lambda: numeric_close(name="n", abs_tol="1"),  # type: ignore[arg-type]
        lambda: matches_model(dict, name="m", version="1"),  # type: ignore[arg-type]
        lambda: matches_model(Answer, name="m", version=""),
        lambda: json_valid(name=""),
    ],
)
def test_bad_arguments_raise_at_construction(make: Any) -> None:
    with pytest.raises((ValueError, TypeError, re.error)):
        make()


def test_invalid_json_schema_raises_at_construction() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    with pytest.raises(jsonschema.SchemaError):
        json_schema({"type": "not-a-type"}, name="s")


def test_json_schema_without_extra_gives_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_jsonschema(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "jsonschema" or name.startswith("jsonschema."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_jsonschema)
    with pytest.raises(ImportError, match=r"evalgate-ai\[schema\]"):
        json_schema({"type": "object"}, name="s")


# --- versions ------------------------------------------------------------------------------


def test_versions_have_the_documented_shape() -> None:
    assert VERSION.match(json_valid(name="j").version)
    assert VERSION.match(length(name="l", max=3).version)


@pytest.mark.parametrize(
    "a,b",
    [
        (lambda: length(name="x", max=2000), lambda: length(name="x", max=500)),
        (lambda: regex("a", name="x"), lambda: regex("b", name="x")),
        (lambda: regex("a", name="x"), lambda: regex("a", name="x", must_match=False)),
        (lambda: contains("a", name="x"), lambda: not_contains("a", name="x")),
        (lambda: contains("a", name="x"), lambda: contains("a", name="x", ignore_case=True)),
        (lambda: exact_match(name="x"), lambda: exact_match(name="x", normalize=["strip"])),
        (lambda: exact_match(name="x"), lambda: exact_match(name="x", field="gold")),
        (
            lambda: numeric_close(name="x", abs_tol=0.1),
            lambda: numeric_close(name="x", abs_tol=0.2),
        ),
        (
            lambda: matches_model(Answer, name="x", version="1"),
            lambda: matches_model(Answer, name="x", version="2"),
        ),
        (
            lambda: matches_model(Answer, name="x", version="1"),
            lambda: matches_model(Strict, name="x", version="1"),
        ),
    ],
)
def test_changing_an_argument_changes_the_version(a: Any, b: Any) -> None:
    assert a().version != b().version


def test_same_arguments_give_the_same_version() -> None:
    assert length(name="x", max=5).version == length(name="other", max=5).version
    assert (
        exact_match(name="x", normalize=["casefold", "strip"]).version
        == exact_match(name="x", normalize=("strip", "casefold", "strip")).version
    )
    assert (
        numeric_close(name="x", abs_tol=1).version == numeric_close(name="x", abs_tol=1.0).version
    )


def test_json_schema_version_tracks_schema() -> None:
    pytest.importorskip("jsonschema")
    a = json_schema({"type": "object"}, name="s").version
    assert a == json_schema({"type": "object"}, name="s").version
    assert a != json_schema({"type": "array"}, name="s").version
