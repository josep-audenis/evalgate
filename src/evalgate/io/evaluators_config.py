"""Load evaluators.yaml and build its evaluators (`evalgate evaluate`, docs/evaluators.md)."""

from pathlib import Path
from typing import NamedTuple

import yaml

from evalgate._json import loads_strict
from evalgate.evaluators import builtins
from evalgate.evaluators.core import Evaluator
from evalgate.io.reader import InputError, _error, _parse, _read_text, _UniqueKeyLoader
from evalgate.models.evaluators_config import (
    ContainsSpec,
    EvaluatorsConfig,
    EvaluatorSpec,
    ExactMatchSpec,
    JsonSchemaSpec,
    JsonValidSpec,
    LengthSpec,
    NumericCloseSpec,
    RegexSpec,
)
from evalgate.models.mapping import ImportManifest


class EvaluatorsSetup(NamedTuple):
    manifest: ImportManifest
    evaluators: tuple[Evaluator, ...]
    store_output: bool


def _schema_file(config_path: Path, ref: str) -> dict[str, object]:
    base = config_path.resolve().parent
    path = (base / ref).resolve()
    if not path.is_relative_to(base):  # no reading files outside the config's directory
        raise ValueError(f"schema_file {ref!r} is outside the config's directory")
    if not path.is_file():
        raise ValueError(f"schema_file {ref!r} not found")
    try:
        # As strict as inline YAML: duplicate keys and NaN/Infinity are errors, not dropped.
        data = loads_strict(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"schema_file {ref!r} is not readable JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"schema_file {ref!r} must hold a JSON object")
    return data


def _build(spec: EvaluatorSpec, config_path: Path) -> Evaluator:
    match spec:
        case JsonValidSpec():
            return builtins.json_valid(name=spec.name)
        case JsonSchemaSpec():
            schema = spec.schema_
            if schema is None:
                assert spec.schema_file is not None  # one of the two, by the model
                schema = _schema_file(config_path, spec.schema_file)
            return builtins.json_schema(schema, name=spec.name)
        case RegexSpec():
            return builtins.regex(spec.pattern, name=spec.name, must_match=spec.must_match)
        case ContainsSpec():
            factory = builtins.contains if spec.type == "contains" else builtins.not_contains
            return factory(spec.text, name=spec.name, ignore_case=spec.ignore_case)
        case ExactMatchSpec():
            return builtins.exact_match(name=spec.name, field=spec.field, normalize=spec.normalize)
        case LengthSpec():
            return builtins.length(name=spec.name, min=spec.min, max=spec.max, unit=spec.unit)
        case NumericCloseSpec():
            return builtins.numeric_close(
                name=spec.name, field=spec.field, abs_tol=spec.abs_tol, rel_tol=spec.rel_tol
            )
    raise AssertionError(f"unhandled evaluator spec {spec!r}")  # pragma: no cover


def load_evaluators_config(path: Path) -> EvaluatorsSetup:
    """Parse evaluators.yaml and build its evaluators. Every problem is an InputError."""
    try:
        data = yaml.load(_read_text(path), Loader=_UniqueKeyLoader)  # SafeLoader subclass
    except yaml.YAMLError as exc:
        raise InputError([_error("evaluators_yaml", f"{path}: invalid YAML: {exc}")]) from exc
    config = _parse(EvaluatorsConfig, data, str(path))

    diagnostics = []
    evaluators: list[Evaluator] = []
    for i, spec in enumerate(config.evaluators):
        try:
            evaluators.append(_build(spec, path))
        except AssertionError:
            raise  # an internal bug, not a config problem
        except Exception as exc:  # factory argument errors, invalid schema, missing extra
            where = f"{path}: evaluators.{i} ({spec.name})"
            # jsonschema errors carry a short `message`; str() dumps the whole metaschema.
            message = getattr(exc, "message", None)
            detail = message if isinstance(message, str) else exc
            diagnostics.append(_error("evaluators_config", f"{where}: {detail}"))
    names = [e.name for e in config.evaluators]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        diagnostics.append(
            _error("evaluators_config", f"{path}: duplicate evaluator names: {', '.join(dupes)}")
        )
    if diagnostics:
        raise InputError(diagnostics)
    return EvaluatorsSetup(config.manifest, tuple(evaluators), config.store_output)
