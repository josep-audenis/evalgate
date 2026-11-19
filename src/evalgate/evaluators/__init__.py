"""Optional native evaluators: turn an output into scores and checks before comparison."""

from evalgate.evaluators.core import Applied, Evaluator, apply, check, score

__all__ = ["Applied", "Evaluator", "apply", "check", "score"]
