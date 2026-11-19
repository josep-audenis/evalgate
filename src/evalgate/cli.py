"""EvalGate command-line interface.

Exit codes: 0 = PASS, 1 = FAIL (policy violated), 2 = INVALID input or tool error.
"""

import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, ParamSpec

import typer
from rich.console import Console
from rich.markup import escape

from evalgate._version import __version__
from evalgate.api import compare_many
from evalgate.comparison.history import trend as history_trend
from evalgate.comparison.repeats import repeats_per_case
from evalgate.io import (
    InputError,
    load_run,
    write_html_report,
    write_json_report,
    write_markdown_report,
    write_policy_template,
)
from evalgate.io.deepeval import import_deepeval
from evalgate.io.evaluate import evaluate_outputs
from evalgate.io.history import load_entry_report, read_history, record_report, record_run
from evalgate.io.importer import import_results
from evalgate.io.labels import calibrate_run
from evalgate.io.promptfoo import import_promptfoo
from evalgate.models.history import MAX_LABEL, RunEntry
from evalgate.reporting.terminal import (
    render,
    render_calibration,
    render_candidates,
    render_diagnostics,
    render_history_list,
    render_run_entry,
    render_trend,
)
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
    # A run_id like "日本" printed to a cp1252 pipe would raise UnicodeEncodeError mid-report and
    # turn a PASS into exit 2: replace unencodable characters. Here, not at import, so importing
    # evalgate.cli never touches the caller's streams.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


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


def _artifact_path(path: Path | None, n: int, many: bool) -> Path | None:
    # str.replace, never str.format: other braces and Windows backslashes stay as they are.
    if path is None or not many:
        return path
    return Path(str(path).replace("{n}", str(n)))


@app.command()
@_fail_closed
def compare(
    baseline: Path,
    candidates: Annotated[list[Path], typer.Argument(metavar="CANDIDATE...")],
    policy: Annotated[Path, typer.Option("--policy")] = Path("evalgate.yaml"),
    json_out: Annotated[
        Path | None,
        typer.Option("--json", help="Write the machine-readable report here ({n}: candidate)."),
    ] = None,
    html_out: Annotated[
        Path | None,
        typer.Option("--report", help="Write a standalone HTML report here ({n}: candidate)."),
    ] = None,
    md_out: Annotated[
        Path | None,
        typer.Option(
            "--markdown", help="Write a Markdown summary here (PR comment, $GITHUB_STEP_SUMMARY)."
        ),
    ] = None,
    history: Annotated[
        Path | None,
        typer.Option("--history", help="Also record the result in this local history store."),
    ] = None,
    label: Annotated[
        str | None, typer.Option("--label", help="Name the history entry (e.g. a release tag).")
    ] = None,
    summary_out: Annotated[
        Path | None,
        typer.Option("--summary", help="Write a JSON summary of all candidates here."),
    ] = None,
    rank_by: Annotated[
        str | None,
        typer.Option("--rank-by", help="Rank candidates on this policy metric (informational)."),
    ] = None,
) -> None:
    """Compare one or more candidate runs against a baseline under a release policy.

    Exit 2 if any candidate is INVALID, else 1 if any FAILs, else 0. With several candidates,
    artifact paths must contain {n} (the candidate's position).
    """
    many = len(candidates) > 1
    usage = None
    if label is not None and history is None:
        usage = "--label only applies with --history"
    elif label is not None and len(label) > MAX_LABEL:
        usage = f"--label longer than {MAX_LABEL} characters"
    elif many and (
        missing := [
            flag
            for flag, value in (
                ("--json", json_out),
                ("--report", html_out),
                ("--markdown", md_out),
            )
            if value is not None and "{n}" not in str(value)
        ]
    ):
        usage = f"with several candidates, {', '.join(missing)} must contain {{n}}"
    if usage is None:
        try:
            result = compare_many(baseline, candidates, policy, rank_by=rank_by)
        except ValueError as exc:
            usage = str(exc)
    if usage is not None:
        console.print(f"[bold yellow]INVALID[/]: {escape(usage)}", highlight=False)
        raise typer.Exit(EXIT_INVALID)

    # Order: decide, artifacts (in order), summary, history, render; a failure stops writing,
    # but everything is still rendered before exiting 2.
    write_error: str | None = None
    history_error: InputError | None = None
    writes: list[tuple[Path, Callable[[], None]]] = [
        (path, functools.partial(writer, report, path))
        for n, report in enumerate(result.reports, 1)
        for writer, template in (
            (write_json_report, json_out),
            (write_html_report, html_out),
            (write_markdown_report, md_out),
        )
        if (path := _artifact_path(template, n, many)) is not None
    ]
    if summary_out is not None:
        writes.append(
            (summary_out, functools.partial(write_json_report, result.summary(), summary_out))
        )
    for path, write in writes:
        try:
            write()
        except Exception as exc:  # any writer failure: keep the decisions visible, then exit 2
            write_error = f"cannot write {path}: {getattr(exc, 'strerror', None) or repr(exc)}"
            break
    recorded = 0
    if write_error is None and history is not None:
        for report in result.reports:
            try:
                record_report(report, history, label=label)
            except InputError as exc:
                history_error = exc
                break
            recorded += 1

    if many or rank_by is not None:
        for n, (path, report) in enumerate(zip(candidates, result.reports, strict=True), 1):
            console.print(f"\n[bold]candidate {n}:[/] {escape(str(path))}", highlight=False)
            render(report, console)
        render_candidates(result.summary(), candidates, console)
    else:
        render(result.reports[0], console)
    if write_error is not None:
        console.print(
            f"[bold yellow]{escape(write_error)}[/]: exit 2 (see the decision above)",
            highlight=False,
        )
        raise typer.Exit(EXIT_INVALID)
    if history_error is not None:  # a tool error: the decision above stands, CI must still stop
        render_diagnostics(history_error.diagnostics, console)
        done = f"only {recorded} of {len(result.reports)} recorded" if many else "not recorded"
        console.print(f"[bold yellow]history {done}[/]: exit 2 (see the decision above)")
        raise typer.Exit(EXIT_INVALID)
    raise typer.Exit(result.exit_code)


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


@app.command()
@_fail_closed
def evaluate(
    outputs: Path,
    config: Annotated[
        Path, typer.Option("--config", help="evaluators.yaml: manifest and built-in evaluators.")
    ],
    out: Annotated[Path, typer.Option("--out", help="Run directory to write.")],
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="Run id (default: manifest.run_id).")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace an existing run.")
    ] = False,
) -> None:
    """Score OUTPUTS.jsonl with built-in evaluators and write an EvalGate run directory.

    Each line: case_id, status (ok/error), output (when ok), and optionally expected,
    scores, metrics, metadata, repeat, error. Outputs are not stored unless the config says so.
    """
    try:
        summary = evaluate_outputs(outputs, config, out, run_id=run_id, overwrite=overwrite)
    except InputError as exc:
        render_diagnostics(exc.diagnostics, console)
        console.print("[bold yellow]INVALID[/]: nothing was written")
        raise typer.Exit(EXIT_INVALID) from exc
    console.print(
        f"[bold green]evaluated[/]: {summary.cases} cases"
        + (f" x {summary.repeats} repeats" if summary.repeats > 1 else "")
        + f" -> {escape(str(out))}",
        highlight=False,
    )
    if summary.evaluator_errors:
        console.print(
            f"[bold yellow]{summary.evaluator_errors} evaluator error(s)[/]: recorded as "
            "status error (see cases.jsonl); they count against max_execution_errors",
            highlight=False,
        )


@app.command()
@_fail_closed
def calibrate(
    run_dir: Path,
    labels: Annotated[
        Path, typer.Option("--labels", help="labels.jsonl: human values per case_id.")
    ],
    json_out: Annotated[
        Path | None, typer.Option("--json", help="Write the calibration report here.")
    ] = None,
) -> None:
    """Measure agreement between a run's checks/scores and human labels.

    Reporting only: accuracy and Cohen's kappa for checks, MAE and Spearman for scores.
    Exits 0 with a report, 2 on bad input; never 1, and never feeds a policy.
    """
    try:
        report = calibrate_run(run_dir, labels)
    except InputError as exc:
        render_diagnostics(exc.diagnostics, console)
        console.print("[bold yellow]INVALID[/]: no calibration report")
        raise typer.Exit(EXIT_INVALID) from exc
    if json_out:
        write_json_report(report, json_out)
    render_calibration(report, console)


history_app = typer.Typer(
    no_args_is_help=True,
    help="Local history of compares and runs: record, list, show, trend (docs/history.md).",
)
app.add_typer(history_app, name="history")

HistoryDir = Annotated[Path, typer.Option("--history", help="History store directory.")]
Last = Annotated[int | None, typer.Option("--last", min=0, help="Only the last N.")]
AsJson = Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")]


def _invalid(exc: InputError, what: str) -> typer.Exit:
    render_diagnostics(exc.diagnostics, console)
    console.print(f"[bold yellow]INVALID[/]: {what}")
    return typer.Exit(EXIT_INVALID)


def _echo_json(data: object) -> None:
    typer.echo(json.dumps(data, indent=2))  # ASCII: survives any console encoding


@history_app.command("add")
@_fail_closed
def history_add(
    run_dir: Path,
    history: HistoryDir,
    label: Annotated[str | None, typer.Option("--label", help="Name the entry.")] = None,
) -> None:
    """Record a summary of a valid run (means, pass rates, failing checks; never outputs)."""
    try:
        entry = record_run(run_dir, history, label=label)
    except InputError as exc:
        raise _invalid(exc, "not recorded") from exc
    console.print(f"[bold green]recorded[/] run entry {entry.id} in {escape(str(history))}")


@history_app.command("list")
@_fail_closed
def history_list(history: HistoryDir, last: Last = None, as_json: AsJson = False) -> None:
    """List entries in recording order."""
    try:
        entries = read_history(history)
    except InputError as exc:
        raise _invalid(exc, "history unreadable") from exc
    if last is not None:
        entries = entries[-last:] if last else []
    if as_json:
        _echo_json({"history_version": 1, "entries": [x.model_dump(mode="json") for x in entries]})
    else:
        render_history_list(entries, console)


@history_app.command("show")
@_fail_closed
def history_show(entry_id: int, history: HistoryDir, as_json: AsJson = False) -> None:
    """Show one entry: a compare's stored report, or a run's summary."""
    try:
        entry = next((x for x in read_history(history) if x.id == entry_id), None)
        if not isinstance(entry, RunEntry):  # a compare, or unknown (history_id)
            compare_entry, report = load_entry_report(history, entry_id)
    except InputError as exc:
        raise _invalid(exc, "entry not shown") from exc
    if isinstance(entry, RunEntry):
        if as_json:
            _echo_json({"entry": entry.model_dump(mode="json")})
        else:
            render_run_entry(entry, console)
        return
    if as_json:
        _echo_json(
            {
                "entry": compare_entry.model_dump(mode="json"),
                "report": report.model_dump(mode="json"),
            }
        )
    else:
        render(report, console)


@history_app.command("trend")
@_fail_closed
def history_trend_cmd(
    path: str,
    history: HistoryDir,
    kind: Annotated[
        Literal["run", "compare"],
        typer.Option("--kind", help="run (default) or compare; never mixed."),
    ] = "run",
    dataset: Annotated[
        str | None, typer.Option("--dataset", help="Only this dataset: ID or ID@VERSION.")
    ] = None,
    application: Annotated[
        str | None, typer.Option("--application", help="Only this (candidate) application.")
    ] = None,
    last: Last = None,
    as_json: AsJson = False,
) -> None:
    """One metric path (e.g. scores.quality) across entries, in recording order."""
    try:
        entries = read_history(history)
    except InputError as exc:
        raise _invalid(exc, "history unreadable") from exc
    result = history_trend(
        entries, path, kind=kind, dataset=dataset, application=application, last=last
    )
    if as_json:
        _echo_json(result)
    else:
        render_trend(result, console)


if __name__ == "__main__":  # python -m evalgate.cli
    app()
