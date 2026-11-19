"""Optional native evaluators: turn an output into scores and checks before comparison."""

from evalgate.evaluators.builtins import (
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
from evalgate.evaluators.core import Applied, Evaluator, apply, check, score
from evalgate.evaluators.judge import Rubric, judge

__all__ = [
    "Applied",
    "Evaluator",
    "Rubric",
    "apply",
    "check",
    "contains",
    "exact_match",
    "json_schema",
    "json_valid",
    "judge",
    "length",
    "matches_model",
    "not_contains",
    "numeric_close",
    "regex",
    "score",
]
