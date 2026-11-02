"""EvalGate command-line interface.

Exit codes: 0 = PASS, 1 = FAIL (policy violated), 2 = INVALID input or tool error.
"""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, ParamSpec

import typer
from rich.console import Console

from evalgate._version import __version__
from evalgate.api import compare as run_compare
from evalgate.io import (
    InputError,
    load_run,
    write_html_report,
    write_json_report,
    write_policy_template,
)
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
    html_out: Annotated[
        Path | None, typer.Option("--report", help="Write a standalone HTML report here.")
    ] = None,
) -> None:
    """Compare a candidate run against a baseline under a release policy."""
    report = run_compare(baseline, candidate, policy)
    render(report, console)
    if json_out:
        write_json_report(report, json_out)
    if html_out:
        write_html_report(report, html_out)
    raise typer.Exit(EXIT_CODES[report.status])


@app.command()
@_fail_closed
def init(
    path: Annotated[Path, typer.Argument()] = Path("evalgate.yaml"),
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Write a documented example release policy."""
    try:
        write_policy_template(path, force)
    except FileExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_INVALID) from exc
    console.print(f"wrote {path}: edit metrics and checks to match your cases.jsonl")
