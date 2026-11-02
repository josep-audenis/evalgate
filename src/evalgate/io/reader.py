"""Load run directories and policy files. Problems become diagnostics, not tracebacks."""

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from evalgate.models import CaseResult, Diagnostic, Policy, Run, RunManifest


class InputError(Exception):
    """Input could not be loaded. Carries every diagnostic found, not just the first."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("; ".join(d.message for d in diagnostics))
        self.diagnostics = diagnostics


def _error(code: str, message: str) -> Diagnostic:
    return Diagnostic(level="error", code=code, message=message)


def _schema_errors(source: str, exc: ValidationError) -> list[Diagnostic]:
    return [
        _error("schema", f"{source}: {'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}")
        for e in exc.errors()
    ]


M = TypeVar("M", bound=BaseModel)


def _parse(model: type[M], data: str | Any, source: str) -> M:
    try:
        if isinstance(data, str):
            return model.model_validate_json(data)
        return model.model_validate(data)
    except ValidationError as exc:
        raise InputError(_schema_errors(source, exc)) from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InputError([_error("file_missing", f"{path}: file not found")]) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError([_error("file_unreadable", f"{path}: {exc}")]) from exc


def load_run(run_dir: Path) -> Run:
    """Read `run.json` + `cases.jsonl`. Raises InputError listing every bad file and line."""
    if not run_dir.is_dir():
        raise InputError([_error("run_dir_missing", f"{run_dir}: not a directory")])

    diagnostics: list[Diagnostic] = []
    manifest: RunManifest | None = None
    cases: list[CaseResult] = []

    manifest_path = run_dir / "run.json"
    try:
        manifest = _parse(RunManifest, _read_text(manifest_path), str(manifest_path))
    except InputError as exc:
        diagnostics += exc.diagnostics

    cases_path = run_dir / "cases.jsonl"
    try:
        lines = _read_text(cases_path).splitlines()
    except InputError as exc:
        diagnostics += exc.diagnostics
    else:
        bad_lines = 0
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                cases.append(_parse(CaseResult, line, f"{cases_path}:{lineno}"))
            except InputError as exc:
                diagnostics += exc.diagnostics
                bad_lines += 1
        if not cases and not bad_lines:
            diagnostics.append(_error("no_cases", f"{cases_path}: contains no cases"))

    if diagnostics or manifest is None:
        raise InputError(diagnostics)
    return Run(manifest=manifest, cases=cases)


def load_policy(path: Path) -> Policy:
    try:
        data = yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise InputError([_error("policy_yaml", f"{path}: invalid YAML: {exc}")]) from exc
    return _parse(Policy, data, str(path))
