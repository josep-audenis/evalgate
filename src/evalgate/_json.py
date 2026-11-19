"""Strict JSON parsing shared by evaluators, the outputs and labels readers, and the judge.

Errors use fixed text: a parsed document can be an output or a prompt, so no key or value from
it is ever echoed. Callers catch ValueError (json.JSONDecodeError is one) and RecursionError.
"""

import json
from math import isfinite
from typing import Any, NoReturn


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant {value}")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _finite_float(text: str) -> float:
    value = float(text)
    if not isfinite(value):  # e.g. 1e999: json would turn it into inf, then null on write
        raise ValueError("number out of range")
    return value


def loads_strict(text: str, *, finite: bool = True) -> Any:
    """Parse JSON rejecting NaN/Infinity, duplicate keys and (if `finite`) numbers like 1e999.

    `finite=False` keeps the 0.7 behaviour of the versioned built-ins (`json_valid` etc.).
    """
    return json.loads(
        text,
        object_pairs_hook=_unique,
        parse_constant=_reject_constant,
        parse_float=_finite_float if finite else float,
    )
