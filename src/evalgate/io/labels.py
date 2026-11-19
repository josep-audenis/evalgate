"""Read human labels (labels.jsonl) and calibrate a run directory against them."""

from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from evalgate._json import loads_strict
from evalgate.calibration import calibrate
from evalgate.io.reader import InputError, _error, _read_text, load_run
from evalgate.models.calibration import CalibrationReport, LabelLine
from evalgate.validation import validate_run


def read_labels(path: Path) -> list[LabelLine]:
    """Parse labels.jsonl. Every bad line is reported, by line number; values never are."""
    text = _read_text(path).removeprefix("﻿")
    labels: list[LabelLine] = []
    lines: list[int] = []
    diagnostics = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            data = loads_strict(raw)
        except (ValueError, RecursionError) as exc:
            msg = getattr(exc, "msg", None) or str(exc)  # a position or fixed text, no content
            diagnostics.append(_error("labels_parse", f"{path}: line {n}: not valid JSON: {msg}"))
            continue
        if not isinstance(data, dict):
            diagnostics.append(_error("labels_parse", f"{path}: line {n}: not a JSON object"))
            continue
        try:
            labels.append(LabelLine.model_validate(data))
            lines.append(n)
        except ValidationError as exc:
            for e in exc.errors():  # location and message only: no input values
                loc = ".".join(map(str, e["loc"])) or "<line>"
                diagnostics.append(_error("labels_record", f"{path}: line {n}: {loc}: {e['msg']}"))
    counts = Counter(label.case_id for label in labels)
    for label, n in zip(labels, lines, strict=True):
        if counts[label.case_id] > 1:
            diagnostics.append(
                _error("labels_record", f"{path}: line {n}: duplicate case_id: {label.case_id}")
            )
    if not labels and not diagnostics:
        diagnostics.append(_error("labels_empty", f"{path}: contains no labels"))
    if diagnostics:
        raise InputError(diagnostics)
    return labels


def calibrate_run(run_dir: str | Path, labels: str | Path) -> CalibrationReport:
    """Load and validate `run_dir`, read `labels`, and calibrate. Raises InputError on any
    error, including an INVALID run: a run that can't be compared isn't calibrated either."""
    run_dir, labels = Path(run_dir), Path(labels)
    run = load_run(run_dir)
    if errors := [d for d in validate_run(run, str(run_dir)) if d.level == "error"]:
        raise InputError(errors)
    report, diagnostics = calibrate(run, read_labels(labels))
    if diagnostics:
        raise InputError(diagnostics)
    return report
