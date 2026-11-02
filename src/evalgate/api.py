"""Path-based entry point shared by the CLI and the SDK, so both produce identical reports."""

from pathlib import Path

from evalgate.engine import evaluate, invalid_report
from evalgate.io import InputError, load_policy, load_run
from evalgate.models import Diagnostic, Policy, Report, Run


def compare(
    baseline: str | Path,
    candidate: str | Path,
    policy: str | Path = "evalgate.yaml",
) -> Report:
    """Load two run directories and a policy, then decide. Bad input yields an INVALID report."""
    diagnostics: list[Diagnostic] = []
    runs: list[Run] = []
    loaded_policy: Policy | None = None
    for run_dir in (baseline, candidate):
        try:
            runs.append(load_run(Path(run_dir)))
        except InputError as exc:
            diagnostics += exc.diagnostics
    try:
        loaded_policy = load_policy(Path(policy))
    except InputError as exc:
        diagnostics += exc.diagnostics

    if diagnostics or loaded_policy is None:
        return invalid_report(diagnostics)
    return evaluate(runs[0], runs[1], loaded_policy)
