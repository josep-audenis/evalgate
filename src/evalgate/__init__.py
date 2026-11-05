"""EvalGate: AI regression testing and release decision engine."""

from evalgate._version import __version__
from evalgate.api import compare
from evalgate.io import EvalRun, InputError
from evalgate.io.importer import ImportSummary, import_results
from evalgate.io.promptfoo import import_promptfoo
from evalgate.models import Report

__all__ = [
    "EvalRun",
    "ImportSummary",
    "InputError",
    "Report",
    "__version__",
    "compare",
    "import_promptfoo",
    "import_results",
]
