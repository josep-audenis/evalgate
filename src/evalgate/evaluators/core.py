"""Evaluator interface and runner. Pure: no I/O (docs/design/native-evaluators.md §1)."""

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from evalgate.models.run import _unwrap_numpy_bool

Kind = Literal["check", "score"]
EvaluatorFn = Callable[[Any, Mapping[str, Any]], object]


@dataclass(frozen=True)
class Evaluator:
    """A named, versioned evaluator: `fn(output, case)` returns a bool (check) or float (score)."""

    name: str
    version: str
    kind: Kind
    fn: EvaluatorFn

    def __post_init__(self) -> None:
        for field in ("name", "version"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"evaluator {field} must be a non-empty string, got {value!r}")
        if self.kind not in ("check", "score"):
            raise ValueError(f"evaluator kind must be 'check' or 'score', got {self.kind!r}")
        if not callable(self.fn):
            raise TypeError(f"evaluator {self.name}: fn must be callable")

    def __call__(self, output: Any, case: Mapping[str, Any]) -> object:
        """Call the wrapped function directly (for unit-testing an evaluator)."""
        return self.fn(output, case)


def check(*, name: str, version: str) -> Callable[[EvaluatorFn], Evaluator]:
    """Decorate `fn(output, case) -> bool` as a check evaluator."""
    return lambda fn: Evaluator(name, version, "check", fn)


def score(*, name: str, version: str) -> Callable[[EvaluatorFn], Evaluator]:
    """Decorate `fn(output, case) -> float` as a score evaluator."""
    return lambda fn: Evaluator(name, version, "score", fn)


class Applied(NamedTuple):
    """Result of running evaluators on one output. `failed` names the first evaluator that
    raised or returned the wrong type; then `scores` and `checks` are empty."""

    scores: dict[str, float]
    checks: dict[str, bool]
    failed: str | None = None


def _result(ev: Evaluator, value: object) -> bool | float | None:
    """The value in its recorded type, or None when the type is wrong (never coerced)."""
    if ev.kind == "check":
        value = _unwrap_numpy_bool(value)
        return value if type(value) is bool else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def apply(evaluators: Sequence[Evaluator], output: Any, case: Mapping[str, Any]) -> Applied:
    """Run evaluators in order. Fail closed: the first exception or wrong-typed result stops
    the run and is reported by name only, since exception text can quote the output."""
    names = [ev.name for ev in evaluators]
    if len(set(names)) != len(names):  # a later result would silently overwrite a failing one
        raise ValueError(
            f"duplicate evaluator names: {sorted({n for n in names if names.count(n) > 1})}"
        )
    scores: dict[str, float] = {}
    checks: dict[str, bool] = {}
    for ev in evaluators:
        # Deep copies per evaluator: none can change the output or case that the caller or a
        # later evaluator sees (an uncopyable output is an evaluator error).
        view = MappingProxyType(copy.deepcopy(dict(case)))
        try:
            value = _result(ev, ev.fn(copy.deepcopy(output), view))
        except Exception:  # not BaseException: Ctrl-C aborts the run, which discards it
            value = None
        if value is None:
            return Applied({}, {}, ev.name)
        if ev.kind == "check":
            checks[ev.name] = bool(value)
        else:
            scores[ev.name] = float(value)
    return Applied(scores, checks)
