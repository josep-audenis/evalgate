"""Built-in deterministic evaluators (docs/design/native-evaluators.md §2).

Each factory requires `name=` and returns an `Evaluator` whose version is
`<impl_version>+<sha256 of its canonical arguments>[:12]`, so changing an argument changes the
version and makes a comparison INVALID. A bad output fails the check (`False`); a check that
can't run (missing case field) raises, which `apply` turns into an evaluator error.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from evalgate._json import loads_strict
from evalgate.evaluators.core import Evaluator

_IMPL = "1"  # bump when any built-in's behaviour changes
_NORMALIZE = ("strip", "casefold")


def _version(factory: str, args: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"factory": factory, **args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return f"{_IMPL}+{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


def _make(factory: str, name: str, args: Mapping[str, Any], fn: Any) -> Evaluator:
    return Evaluator(name, _version(factory, args), "check", fn)


class _Invalid(Exception):
    """The output is not strict JSON."""


def _strict_json(output: object) -> Any:
    """Parse strict JSON text (no NaN/Infinity, no duplicate keys) or raise `_Invalid`."""
    if not isinstance(output, str):
        raise _Invalid
    try:
        return loads_strict(output, finite=False)
    except (ValueError, RecursionError) as exc:
        raise _Invalid from exc


def json_valid(*, name: str) -> Evaluator:
    """Passes when the output is a string of strict JSON."""

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        try:
            _strict_json(output)
        except _Invalid:
            return False
        return True

    return _make("json_valid", name, {}, fn)


def matches_model(model: type[BaseModel], *, name: str, version: str) -> Evaluator:
    """Passes when the output (JSON text or a Python value) validates against a Pydantic model.

    `version` is required, and must be bumped when the model changes in ways its JSON schema
    can't show: validator code, custom types, or config such as strict mode. JSON text is parsed
    as strictly as `json_valid` (no NaN/Infinity, no duplicate keys) before validation.
    """
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError("matches_model needs a pydantic BaseModel subclass")
    if not isinstance(version, str) or not version:
        raise ValueError("matches_model needs a non-empty version=")
    schema = model.model_json_schema()

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        try:
            if isinstance(output, bytes):
                output = output.decode("utf-8")
            if isinstance(output, str):
                _strict_json(output)
                model.model_validate_json(output)
            else:
                model.model_validate(output)
        except (_Invalid, ValidationError, UnicodeDecodeError, RecursionError):
            return False
        return True

    return _make("matches_model", name, {"schema": schema, "version": version}, fn)


def json_schema(schema: Mapping[str, Any], *, name: str) -> Evaluator:
    """Passes when the output is strict JSON valid under a JSON Schema (Draft 2020-12).

    Needs the optional extra: `pip install evalgate-ai[schema]`. Offline: only local `$ref`s
    ("#...") are allowed; a remote one raises at construction. `format` is an annotation only
    (not asserted), as Draft 2020-12 specifies by default.
    """
    try:
        from importlib.metadata import version as dist_version

        from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
        from referencing import Registry
    except ImportError as exc:
        raise ImportError(
            "json_schema needs the optional jsonschema dependency: "
            "pip install 'evalgate-ai[schema]'"
        ) from exc
    # Canonical copy: the validator and the version hash see exactly the same JSON.
    canonical = json.loads(json.dumps(dict(schema), allow_nan=False))
    remote = sorted(_remote_refs(canonical))
    if remote:
        raise ValueError(f"json_schema allows only local $refs (offline): {remote}")
    Draft202012Validator.check_schema(canonical)  # raises SchemaError for an invalid schema
    # Empty registry: never fall back to jsonschema's network retrieval.
    validator = Draft202012Validator(canonical, registry=Registry())
    major = dist_version("jsonschema").split(".")[0]

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        try:
            return bool(validator.is_valid(_strict_json(output)))
        except (_Invalid, RecursionError):
            return False

    return _make("json_schema", name, {"schema": canonical, "jsonschema": major}, fn)


def _remote_refs(node: Any) -> list[str]:
    if isinstance(node, list):
        return [r for item in node for r in _remote_refs(item)]
    if not isinstance(node, dict):
        return []
    found = [
        v
        for k, v in node.items()
        if k in ("$ref", "$dynamicRef") and not (isinstance(v, str) and v.startswith("#"))
    ]
    return found + [r for v in node.values() for r in _remote_refs(v)]


def regex(pattern: str, *, name: str, must_match: bool = True) -> Evaluator:
    """Passes when `pattern` is found in the output (or, with `must_match=False`, is not).

    Uses Python's `re` (no timeout): avoid catastrophic patterns such as `(a+)+$`.
    """
    if not isinstance(pattern, str):
        raise TypeError("regex pattern must be a string")
    if type(must_match) is not bool:
        raise TypeError("regex must_match must be a bool")
    compiled = re.compile(pattern)  # re.error for a bad pattern

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        if not isinstance(output, str):
            return False
        return (compiled.search(output) is not None) == must_match

    return _make("regex", name, {"pattern": pattern, "must_match": must_match}, fn)


def _substring(factory: str, text: str, name: str, ignore_case: bool, present: bool) -> Evaluator:
    if not isinstance(text, str) or not text:
        raise ValueError(f"{factory} needs non-empty text")
    if type(ignore_case) is not bool:
        raise TypeError(f"{factory} ignore_case must be a bool")
    needle = text.casefold() if ignore_case else text

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        if not isinstance(output, str):
            return False
        haystack = output.casefold() if ignore_case else output
        return (needle in haystack) == present

    return _make(factory, name, {"text": text, "ignore_case": ignore_case}, fn)


def contains(text: str, *, name: str, ignore_case: bool = False) -> Evaluator:
    """Passes when the output contains `text`."""
    return _substring("contains", text, name, ignore_case, True)


def not_contains(text: str, *, name: str, ignore_case: bool = False) -> Evaluator:
    """Passes when the output does not contain `text`."""
    return _substring("not_contains", text, name, ignore_case, False)


def _expected(case: Mapping[str, Any], field: str) -> Any:
    if field not in case:
        raise KeyError(field)  # misconfigured, not a bad output: evaluator error
    return case[field]


def exact_match(*, name: str, field: str = "expected", normalize: Iterable[str] = ()) -> Evaluator:
    """Passes when the output equals `case[field]`, after the chosen `normalize` steps
    ("strip", "casefold") for strings. Values of different types never match."""
    if not isinstance(field, str) or not field:
        raise ValueError("exact_match field must be a non-empty string")
    steps = tuple(dict.fromkeys(normalize))
    unknown = [s for s in steps if s not in _NORMALIZE]
    if isinstance(normalize, str) or unknown:
        raise ValueError(f"normalize must be a collection of {_NORMALIZE}, got {normalize!r}")

    def norm(value: str) -> str:
        if "strip" in steps:
            value = value.strip()
        return value.casefold() if "casefold" in steps else value

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        expected = _expected(case, field)
        if isinstance(output, str) and isinstance(expected, str):
            return norm(output) == norm(expected)
        return _same(output, expected)

    args = {"field": field, "normalize": sorted(steps)}
    return _make("exact_match", name, args, fn)


def _same(a: Any, b: Any) -> bool:
    """Equality that also requires equal types at every level (so True != 1, 1 != 1.0)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def length(
    *,
    name: str,
    min: int | None = None,  # noqa: A002 - design API names
    max: int | None = None,  # noqa: A002
    unit: Literal["chars", "words"] = "chars",
) -> Evaluator:
    """Passes when the output string's length (characters or whitespace-separated words) is
    within [min, max]."""
    bounds = [b for b in (min, max) if b is not None]
    if not bounds:
        raise ValueError("length needs min= or max=")
    if any(isinstance(b, bool) or not isinstance(b, int) or b < 0 for b in bounds):
        raise ValueError("length bounds must be non-negative integers")
    if min is not None and max is not None and min > max:
        raise ValueError("length min must not exceed max")
    if unit not in ("chars", "words"):
        raise ValueError("length unit must be 'chars' or 'words'")

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        if not isinstance(output, str):
            return False
        n = len(output) if unit == "chars" else len(output.split())
        return (min is None or n >= min) and (max is None or n <= max)

    return _make("length", name, {"min": min, "max": max, "unit": unit}, fn)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            number = float(value)
        except OverflowError:
            return None
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def numeric_close(
    *, name: str, field: str = "expected", abs_tol: float = 0.0, rel_tol: float = 0.0
) -> Evaluator:
    """Passes when the output (a number or numeric string) is within tolerance of
    `case[field]` (`math.isclose`). Unparseable output fails the check. Strings are parsed with
    Python's `float()` (so " 1_000 " and "1e3" parse; "0x10" doesn't), and values are compared
    as floats."""
    for label, tol in (("abs_tol", abs_tol), ("rel_tol", rel_tol)):
        if _number(tol) is None or isinstance(tol, str) or tol < 0:
            raise ValueError(f"numeric_close {label} must be a finite number >= 0")

    def fn(output: object, case: Mapping[str, Any]) -> bool:
        expected = _number(_expected(case, field))
        if expected is None or isinstance(case[field], str):
            raise TypeError(f"case[{field!r}] is not a finite number")
        got = _number(output)
        return got is not None and math.isclose(
            got, expected, rel_tol=float(rel_tol), abs_tol=float(abs_tol)
        )

    args = {"field": field, "abs_tol": float(abs_tol), "rel_tol": float(rel_tol)}
    return _make("numeric_close", name, args, fn)
