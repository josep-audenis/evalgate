"""EvalGate command-line interface.

Exit codes: 0 = PASS, 1 = FAIL (policy violated), 2 = INVALID input or tool error.
"""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, ParamSpec

import typer
from rich.console import Console
from rich.markup import escape

from evalgate._version import __version__
from evalgate.api import compare as run_compare
from evalgate.comparison.repeats import repeats_per_case
from evalgate.io import (
    InputError,
    load_run,
    write_html_report,
    write_json_report,
    write_policy_template,
)
from evalgate.io.deepeval import import_deepeval
from evalgate.io.importer import import_results
from evalgate.io.promptfoo import import_promptfoo
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
    k = repeats_per_case(run)
    shape = f"{len(run.cases) // k} cases" + (f" x {k} repeats" if k > 1 else "")
    console.print(f"[bold green]valid[/]: {escape(run.manifest.run_id)}, {shape}")


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
    # Artifacts first: a terminal rendering problem must not lose the machine-readable result.
    if json_out:
        write_json_report(report, json_out)
    if html_out:
        write_html_report(report, html_out)
    render(report, console)
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


@app.command(name="import")
@_fail_closed
def import_(
    source: Path,
    out: Annotated[Path, typer.Option("--out", help="Run directory to write.")],
    mapping: Annotated[
        Path | None, typer.Option("--mapping", help="Mapping YAML (generic CSV / JSON import).")
    ] = None,
    from_: Annotated[
        str | None,
        typer.Option("--from", help="Source tool with a built-in adapter: promptfoo, deepeval."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Run id (default: mapping run_id, promptfoo evalId, DeepEval identifier/file).",
        ),
    ] = None,
    dataset: Annotated[str | None, typer.Option("--dataset", help="Adapters: dataset id.")] = None,
    dataset_version: Annotated[
        str | None, typer.Option("--dataset-version", help="Adapters: dataset version.")
    ] = None,
    evaluator_version: Annotated[
        str | None, typer.Option("--evaluator-version", help="Adapters: evaluator version.")
    ] = None,
    case_id: Annotated[
        str | None,
        typer.Option(
            "--case-id",
            help="promptfoo: description (default), vars.<name>, metadata.<name>. "
            "deepeval: name (default), metadata.<key>.",
        ),
    ] = None,
    provider: Annotated[
        str | None, typer.Option("--provider", help="promptfoo: provider label or id to import.")
    ] = None,
    prompt: Annotated[
        str | None, typer.Option("--prompt", help="promptfoo: prompt label or id to import.")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing run.")
    ] = False,
) -> None:
    """Convert results from another tool into an EvalGate run directory.

    Generic: --mapping mapping.yaml (CSV / JSON / JSONL). Adapters: --from promptfoo or
    --from deepeval, with --dataset, --dataset-version and --evaluator-version.
    """
    adapter_only = {
        "--case-id": case_id is not None,
        "--provider": provider,
        "--prompt": prompt,
    }
    manifest = {
        "--dataset": dataset,
        "--dataset-version": dataset_version,
        "--evaluator-version": evaluator_version,
    }
    usage = None
    if from_ is None:
        if mapping is None:
            usage = "pass --mapping for a CSV / JSON file, or --from promptfoo / deepeval"
        elif extra := [k for k, v in (manifest | adapter_only).items() if v]:
            usage = f"{', '.join(extra)} only apply with --from; the mapping sets the manifest"
    elif from_ not in ("promptfoo", "deepeval"):
        usage = f"unknown --from {from_!r}; supported: promptfoo, deepeval"
    elif mapping is not None:
        usage = "--mapping and --from are exclusive"
    elif missing := [k for k, v in manifest.items() if not v]:
        usage = f"--from {from_} needs {', '.join(missing)}"
    elif from_ == "deepeval" and (provider or prompt):
        usage = "--provider and --prompt only apply with --from promptfoo"
    if usage:
        console.print(f"[bold yellow]INVALID[/]: {escape(usage)}")
        raise typer.Exit(EXIT_INVALID)

    try:
        if from_ == "promptfoo":
            assert dataset and dataset_version and evaluator_version  # checked above
            summary = import_promptfoo(
                source,
                out,
                dataset=dataset,
                dataset_version=dataset_version,
                evaluator_version=evaluator_version,
                run_id=run_id,
                case_id="description" if case_id is None else case_id,
                provider=provider,
                prompt=prompt,
                overwrite=overwrite,
            )
        elif from_ == "deepeval":
            assert dataset and dataset_version and evaluator_version  # checked above
            summary = import_deepeval(
                source,
                out,
                dataset=dataset,
                dataset_version=dataset_version,
                evaluator_version=evaluator_version,
                run_id=run_id,
                case_id="name" if case_id is None else case_id,
                overwrite=overwrite,
            )
        else:
            assert mapping is not None  # checked above
            summary = import_results(source, mapping, out, run_id=run_id, overwrite=overwrite)
    except InputError as exc:
        render_diagnostics(exc.diagnostics, console)
        console.print("[bold yellow]INVALID[/]: nothing was written")
        raise typer.Exit(EXIT_INVALID) from exc
    console.print(
        f"[bold green]imported[/]: {summary.cases} cases"
        + (f" x {summary.repeats} repeats" if summary.repeats > 1 else "")
        + f" -> {escape(str(out))}",
        highlight=False,
    )
    if summary.ignored_fields:
        console.print(
            f"[dim]not imported (unmapped): {escape(', '.join(summary.ignored_fields))}[/]",
            highlight=False,
        )
