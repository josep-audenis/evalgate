"""EvalGate command-line interface.

Exit codes: 0 = PASS, 1 = FAIL (policy violated), 2 = INVALID input or tool error.
"""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, ParamSpec

import typer
from rich.console import Console

from evalgate import __version__
from evalgate.engine import evaluate, invalid_report
from evalgate.io import InputError, load_policy, load_run, write_json_report
from evalgate.models import Diagnostic, Policy, Run
from evalgate.reporting.terminal import render, render_diagnostics
from evalgate.validation import validate_run

EXIT_PASS, EXIT_FAIL, EXIT_INVALID = 0, 1, 2
EXIT_CODES = {"PASS": EXIT_PASS, "FAIL": EXIT_FAIL, "INVALID": EXIT_INVALID}

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

P = ParamSpec("P")


def _fail_closed(command: Callable[P, None]) -> Callable[P, None]:
    """Unexpected errors exit 2, never 1, so a crash can't be mistaken for a policy FAIL."""

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            command(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except Exception as exc:
            typer.echo(f"internal error: {exc!r}", err=True)
            raise typer.Exit(EXIT_INVALID) from exc

    return wrapper


def _version(value: bool) -> None:
    if value:
        typer.echo(f"evalgate {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True)] = False,
) -> None:
    """Compare evaluation runs and decide whether a candidate is releasable."""


@app.command()
@_fail_closed
def validate(run_dir: Path) -> None:
    """Check a run directory's structure, completeness and metric validity."""
    try:
        run = load_run(run_dir)
        diagnostics = validate_run(run, str(run_dir))
    except InputError as exc:
        diagnostics = exc.diagnostics
    render_diagnostics(diagnostics, console)
    if any(d.level == "error" for d in diagnostics):
        console.print("[bold yellow]INVALID[/]")
        raise typer.Exit(EXIT_INVALID)
    console.print(f"[bold green]valid[/]: {run.manifest.run_id}, {len(run.cases)} cases")


@app.command()
@_fail_closed
def compare(
    baseline: Path,
    candidate: Path,
    policy: Annotated[Path, typer.Option("--policy")] = Path("evalgate.yaml"),
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Write the machine-readable report here.")
    ] = None,
) -> None:
    """Compare a candidate run against a baseline under a release policy."""
    diagnostics: list[Diagnostic] = []
    runs: list[Run] = []
    loaded_policy: Policy | None = None
    for run_dir in (baseline, candidate):
        try:
            runs.append(load_run(run_dir))
        except InputError as exc:
            diagnostics += exc.diagnostics
    try:
        loaded_policy = load_policy(policy)
    except InputError as exc:
        diagnostics += exc.diagnostics

    if diagnostics or loaded_policy is None:
        report = invalid_report(diagnostics)
    else:
        report = evaluate(runs[0], runs[1], loaded_policy)

    render(report, console)
    if json_out:
        write_json_report(report, json_out)
    raise typer.Exit(EXIT_CODES[report.status])
