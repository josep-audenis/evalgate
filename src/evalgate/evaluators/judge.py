"""LLM judges as ordinary evaluators (docs/design/native-evaluators.md §5, §11).

Stdlib-only and offline: `complete(prompt) -> reply` is the user's own client, so EvalGate never
talks to a model. A reply must be exactly `{"pass": bool}` (check) or `{"score": number}`
(score), optionally with a discarded `"reason"` string; anything else raises, which `apply`
turns into `status: error`, never a default value. The engine never imports this module.
"""

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from evalgate._json import loads_strict
from evalgate.evaluators.core import Evaluator, Kind

_IMPL = "1"  # bump when rendering, parsing or caching behaviour changes
_KEY = {"check": "pass", "score": "score"}


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


@dataclass(frozen=True)
class Rubric:
    """A versioned prompt template. `$output` is the output; any other `$name` is
    `case[name]`. Write a literal dollar sign as `$$`. `scale` (inclusive) is required for
    scores and not allowed for checks."""

    version: str
    kind: Kind
    template: str
    scale: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError(f"rubric version must be a non-empty string, got {self.version!r}")
        if self.kind not in ("check", "score"):
            raise ValueError(f"rubric kind must be 'check' or 'score', got {self.kind!r}")
        if not isinstance(self.template, str):
            raise TypeError("rubric template must be a string")
        template = Template(self.template)
        if not template.is_valid():
            raise ValueError("rubric template has an invalid placeholder; write a literal $ as $$")
        if "output" not in template.get_identifiers():
            raise ValueError("rubric template must use $output")
        if self.kind == "check":
            if self.scale is not None:
                raise ValueError("a check rubric takes no scale")
            return
        if self.scale is None:
            raise ValueError("a score rubric needs scale=(min, max)")
        bounds = tuple(self.scale)
        if len(bounds) != 2 or any(
            isinstance(b, bool) or not isinstance(b, int | float) for b in bounds
        ):
            raise ValueError("scale must be two numbers (min, max)")
        low, high = float(bounds[0]), float(bounds[1])
        if not (math.isfinite(low) and math.isfinite(high)) or low >= high:
            raise ValueError("scale must be finite with min < max")
        object.__setattr__(self, "scale", (low, high))


def _version(rubric: Rubric, model_id: str) -> str:
    # The hash covers the readable prefix too, so a "+" inside either part can't collide.
    digest = hashlib.sha256(
        _canonical(
            {
                "impl": _IMPL,
                "kind": rubric.kind,
                "template": rubric.template,
                "scale": list(rubric.scale) if rubric.scale else None,
                "rubric_version": rubric.version,
                "model_id": model_id,
            }
        ).encode()
    ).hexdigest()
    return f"{rubric.version}+{model_id}+{digest[:12]}"


def _text(value: object) -> str:
    return value if isinstance(value, str) else _canonical(value)


def _value(rubric: Rubric, value: object) -> bool | float | None:
    """`value` in its recorded type if valid for the rubric, else None (never coerced)."""
    if rubric.kind == "check":
        return value if type(value) is bool else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    assert rubric.scale is not None  # checked in Rubric
    try:
        number = float(value)
    except OverflowError:  # an int too large for a float
        return None
    low, high = rubric.scale
    return number if math.isfinite(number) and low <= number <= high else None


def _parse(rubric: Rubric, reply: object) -> bool | float:
    # Fixed messages: the reply can quote the output, and apply() drops the text anyway.
    if not isinstance(reply, str):
        raise TypeError("judge reply must be a string")
    data = loads_strict(reply.strip())
    key = _KEY[rubric.kind]
    if not isinstance(data, dict) or key not in data or set(data) - {key, "reason"}:
        raise ValueError("judge reply does not match the rubric's format")
    if not isinstance(data.get("reason", ""), str):
        raise ValueError("judge reply reason must be a string")
    value = _value(rubric, data[key])
    if value is None:
        raise ValueError("judge reply value is not valid for the rubric")
    return value


def judge(
    rubric: Rubric,
    complete: Callable[[str], str],
    *,
    name: str,
    model_id: str,
    cache_dir: str | Path | None = None,
) -> Evaluator:
    """An evaluator that renders `rubric` for each output and asks `complete` to grade it.

    `model_id` is a label you declare (EvalGate can't check what `complete` calls); it is part
    of the version, so a different model makes a comparison INVALID. Include sampling settings
    in it, e.g. "claude-sonnet-5-5@t0".
    """
    if not isinstance(rubric, Rubric):
        raise TypeError("rubric must be a Rubric")
    if not callable(complete):
        raise TypeError("complete must be callable: (prompt: str) -> str")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError(f"model_id must be a non-empty string, got {model_id!r}")
    template = Template(rubric.template)
    identifiers = template.get_identifiers()

    def fn(output: Any, case: Mapping[str, Any]) -> bool | float:
        # Only the identifiers the template uses; `$output` is always the output. One pass, so
        # a `$name` inside a value is inserted literally.
        values = {i: _text(output if i == "output" else case[i]) for i in identifiers}
        return _parse(rubric, complete(template.substitute(values)))

    return Evaluator(name, _version(rubric, model_id), rubric.kind, fn)
