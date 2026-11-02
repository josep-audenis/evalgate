"""EvalGate: AI regression testing and release decision engine."""

from evalgate._version import __version__
from evalgate.api import compare
from evalgate.io import EvalRun, InputError
from evalgate.models import Report

__all__ = ["EvalRun", "InputError", "Report", "__version__", "compare"]
