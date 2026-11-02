"""EvalGate command-line interface.

Exit codes: 0 = PASS, 1 = FAIL (policy violated), 2 = INVALID input or tool error.
"""

from pathlib import Path
from typing import Annotated

import typer

from evalgate import __version__

EXIT_PASS, EXIT_FAIL, EXIT_INVALID = 0, 1, 2

app = typer.Typer(no_args_is_help=True, add_completion=False)


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
def validate(run_dir: Path) -> None:
    """Check a run directory's structure, completeness and metric validity."""
    typer.echo("validate: not implemented yet", err=True)
    raise typer.Exit(EXIT_INVALID)


@app.command()
def compare(
    baseline: Path,
    candidate: Path,
    policy: Annotated[Path, typer.Option("--policy")] = Path("evalgate.yaml"),
) -> None:
    """Compare a candidate run against a baseline under a release policy."""
    typer.echo("compare: not implemented yet", err=True)
    raise typer.Exit(EXIT_INVALID)
