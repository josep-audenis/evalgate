"""`evalgate evaluate`: score an outputs file with evaluators from evaluators.yaml.

Shares the engine with the SDK: rows go through `write_rows`, so through `EvalRun.record`, and
the files written are byte-identical to recording the same outputs with `EvalRun` directly.
Diagnostics name a line and a field, never the output's text.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from evalgate.io.evaluators_config import load_evaluators_config
from evalgate.io.importer import ImportSummary, _unique_keys, write_rows
from evalgate.io.reader import InputError, _error, _read_text, load_run
from evalgate.models.run import NonEmptyStr, Number


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


class OutputLine(BaseModel):
    """One line of OUTPUTS.jsonl."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: NonEmptyStr
    status: Literal["ok", "error"]
    output: JsonValue = None  # required when ok (checked via model_fields_set)
    error: str | None = None
    expected: JsonValue = None
    metadata: dict[str, Any] = {}
    metrics: dict[str, Number] = {}
    repeat: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _status_fields(self) -> Self:
        has_output = "output" in self.model_fields_set
        if self.status == "ok" and not has_output:
            raise ValueError("`output` is required when status is ok")
        if self.status == "ok" and self.error is not None:
            raise ValueError("`error` is only allowed when status is error")
        if self.status == "error" and has_output:
            raise ValueError("`output` is not allowed when status is error")
        return self


def _row(line: OutputLine) -> dict[str, Any]:
    row: dict[str, Any] = {"case_id": line.case_id, "status": line.status}
    if line.status == "error":
        row["error"] = line.error or "error"
    else:
        # `case` for evaluators: everything about the case except the output and the run data.
        case: dict[str, Any] = {"case_id": line.case_id, "metadata": line.metadata}
        if "expected" in line.model_fields_set:
            case["expected"] = line.expected
        row |= {"output": line.output, "case": case, "metrics": line.metrics}
    if line.metadata:
        row["metadata"] = line.metadata
    if line.repeat is not None:
        row["repeat"] = line.repeat
    return row


def read_outputs(path: Path) -> list[dict[str, Any]]:
    """Parse OUTPUTS.jsonl into write_rows rows. Every bad line is reported; values never are."""
    text = _read_text(path).removeprefix("﻿")
    rows: list[dict[str, Any]] = []
    diagnostics = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as exc:
            # json's message gives a position, not content; ours names the duplicate key.
            msg = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
            diagnostics.append(_error("outputs_parse", f"{path}: line {n}: not valid JSON: {msg}"))
            continue
        if not isinstance(data, dict):
            diagnostics.append(_error("outputs_parse", f"{path}: line {n}: not a JSON object"))
            continue
        try:
            rows.append(_row(OutputLine.model_validate(data)))
        except ValidationError as exc:
            for e in exc.errors():  # location and message only: no input values
                loc = ".".join(map(str, e["loc"])) or "<line>"
                diagnostics.append(_error("outputs_record", f"{path}: line {n}: {loc}: {e['msg']}"))
    if not rows and not diagnostics:
        diagnostics.append(_error("no_cases", f"{path}: contains no records"))
    if diagnostics:
        raise InputError(diagnostics)
    return rows


def evaluate_outputs(
    source: str | Path,
    config: str | Path,
    out: str | Path,
    *,
    run_id: str | None = None,
    overwrite: bool = False,
) -> ImportSummary:
    """Score `source` (OUTPUTS.jsonl) with the evaluators in `config` (evaluators.yaml) and write
    the run directory `out`. Raises InputError on any bad input; then nothing is written."""
    source, config, out = Path(source), Path(config), Path(out)
    setup = load_evaluators_config(config)
    run_id = run_id or setup.manifest.run_id
    if not run_id:
        raise InputError(
            [_error("import_run_id", f"{config}: set manifest.run_id or pass --run-id")]
        )
    rows = read_outputs(source)
    summary = write_rows(
        source,
        rows,
        out,
        run_id,
        setup.manifest,
        overwrite=overwrite,
        ignored_fields=[],
        evaluators=setup.evaluators,
        store_output=setup.store_output,
    )
    written_errors = sum(c.status == "error" for c in load_run(out).cases)
    input_errors = sum(r["status"] == "error" for r in rows)
    return summary._replace(evaluator_errors=written_errors - input_errors)
