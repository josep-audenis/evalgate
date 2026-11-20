"""EvalGate: AI regression testing and release decision engine."""

from evalgate import reporters as reporters
from evalgate._version import __version__
from evalgate.api import MultiResult, compare, compare_many
from evalgate.io import EvalRun, InputError
from evalgate.io.deepeval import import_deepeval
from evalgate.io.evaluate import evaluate_outputs
from evalgate.io.history import load_entry_report, read_history, record_report, record_run
from evalgate.io.importer import ImportSummary, import_results
from evalgate.io.promptfoo import import_promptfoo
from evalgate.models import Report

__all__ = [
    "EvalRun",
    "ImportSummary",
    "MultiResult",
    "InputError",
    "Report",
    "__version__",
    "compare",
    "compare_many",
    "evaluate_outputs",
    "import_deepeval",
    "import_promptfoo",
    "import_results",
    "load_entry_report",
    "read_history",
    "record_report",
    "record_run",
    "reporters",
]
