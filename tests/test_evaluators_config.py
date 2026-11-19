"""evaluators.yaml loading (0.8.0 T1): built-ins from YAML, strict, fail closed."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from evalgate.evaluators import (
    contains,
    exact_match,
    json_schema,
    json_valid,
    length,
    not_contains,
    numeric_close,
    regex,
)
from evalgate.io import InputError
from evalgate.io.evaluators_config import load_evaluators_config

MANIFEST = {"dataset_id": "d", "dataset_version": "v", "evaluator_version": "e"}
SCHEMA = {"type": "object", "required": ["a"]}


def write(tmp_path: Path, evaluators: list[dict[str, Any]], **top: Any) -> Path:
    path = tmp_path / "evaluators.yaml"
    config = {"version": 1, "manifest": MANIFEST, "evaluators": evaluators} | top
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def codes(exc: pytest.ExceptionInfo[InputError]) -> set[str]:
    return {d.code for d in exc.value.diagnostics}


def test_every_builtin_round_trips_with_the_same_versions(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    (tmp_path / "s.json").write_text(json.dumps(SCHEMA))
    path = write(
        tmp_path,
        [
            {"type": "json_valid", "name": "a"},
            {"type": "json_schema", "name": "b", "schema": SCHEMA},
            {"type": "json_schema", "name": "b2", "schema_file": "s.json"},
            {"type": "regex", "name": "c", "pattern": r"\d+", "must_match": False},
            {"type": "contains", "name": "d", "text": "hi", "ignore_case": True},
            {"type": "not_contains", "name": "e", "text": "as an AI"},
            {"type": "exact_match", "name": "f", "normalize": ["strip", "casefold"]},
            {"type": "length", "name": "g", "max": 300, "unit": "words"},
            {"type": "numeric_close", "name": "h", "field": "gold", "abs_tol": 1},
        ],
        store_output=True,
    )
    setup = load_evaluators_config(path)
    expected = [
        json_valid(name="a"),
        json_schema(SCHEMA, name="b"),
        json_schema(SCHEMA, name="b2"),
        regex(r"\d+", name="c", must_match=False),
        contains("hi", name="d", ignore_case=True),
        not_contains("as an AI", name="e"),
        exact_match(name="f", normalize=["strip", "casefold"]),
        length(name="g", max=300, unit="words"),
        numeric_close(name="h", field="gold", abs_tol=1.0),
    ]
    assert [(e.name, e.version) for e in setup.evaluators] == [
        (e.name, e.version) for e in expected
    ]
    assert setup.store_output is True
    assert setup.manifest.dataset_id == "d"


def test_store_output_defaults_off(tmp_path: Path) -> None:
    setup = load_evaluators_config(write(tmp_path, [{"type": "json_valid", "name": "a"}]))
    assert setup.store_output is False


@pytest.mark.parametrize(
    "evaluators,top",
    [
        ([{"type": "json_valid", "name": "a", "typo": 1}], {}),  # unknown key
        ([{"type": "matches_model", "name": "a"}], {}),  # Python-only
        ([{"type": "nope", "name": "a"}], {}),
        ([{"type": "json_valid"}], {}),  # name required
        ([{"type": "json_valid", "name": ""}], {}),
        ([{"type": "json_schema", "name": "a"}], {}),  # neither source
        ([{"type": "json_schema", "name": "a", "schema": {}, "schema_file": "s.json"}], {}),
        ([{"type": "length", "name": "a", "max": "3"}], {}),  # strict types
        ([{"type": "regex", "name": "a", "pattern": "x", "must_match": "yes"}], {}),
        ([{"type": "exact_match", "name": "a", "normalize": ["lower"]}], {}),
        ([], {}),  # at least one evaluator
        ([{"type": "json_valid", "name": "a"}], {"version": 2}),
        ([{"type": "json_valid", "name": "a"}], {"extra_top": True}),
        ([{"type": "json_valid", "name": "a"}], {"store_output": "yes"}),
    ],
)
def test_schema_errors(tmp_path: Path, evaluators: list[dict[str, Any]], top: Any) -> None:
    with pytest.raises(InputError) as exc:
        load_evaluators_config(write(tmp_path, evaluators, **top))
    assert codes(exc) == {"schema"}


@pytest.mark.parametrize(
    "evaluators",
    [
        [{"type": "json_valid", "name": "a"}, {"type": "length", "name": "a", "max": 3}],
        [{"type": "regex", "name": "a", "pattern": "("}],
        [{"type": "length", "name": "a"}],  # needs min or max
        [{"type": "length", "name": "a", "min": 5, "max": 2}],
        [{"type": "contains", "name": "a", "text": ""}],
        [{"type": "numeric_close", "name": "a", "abs_tol": -1}],
        [{"type": "json_schema", "name": "a", "schema": {"type": "nonsense"}}],
        [{"type": "json_schema", "name": "a", "schema": {"$ref": "https://x.test/s.json"}}],
        [{"type": "json_schema", "name": "a", "schema_file": "missing.json"}],
        [{"type": "json_schema", "name": "a", "schema_file": "../outside.json"}],
        [{"type": "json_schema", "name": "a", "schema_file": "bad.json"}],
        [{"type": "json_schema", "name": "a", "schema_file": "list.json"}],
        [{"type": "json_schema", "name": "a", "schema_file": "dupe.json"}],
        [{"type": "json_schema", "name": "a", "schema_file": "nan.json"}],
        [{"type": "json_schema", "name": "a", "schema_file": "."}],
    ],
)
def test_build_errors(tmp_path: Path, evaluators: list[dict[str, Any]]) -> None:
    if any(e.get("type") == "json_schema" for e in evaluators):
        pytest.importorskip("jsonschema")
    sub = tmp_path / "cfg"
    sub.mkdir()
    (tmp_path / "outside.json").write_text("{}")
    (sub / "bad.json").write_text("{not json")
    (sub / "list.json").write_text("[]")
    (sub / "dupe.json").write_text('{"type": "object", "type": "string"}')
    (sub / "nan.json").write_text('{"maximum": NaN}')
    with pytest.raises(InputError) as exc:
        load_evaluators_config(write(sub, evaluators))
    assert codes(exc) == {"evaluators_config"}


def test_every_build_error_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        [{"type": "regex", "name": "a", "pattern": "("}, {"type": "length", "name": "b"}],
    )
    with pytest.raises(InputError) as exc:
        load_evaluators_config(path)
    assert len(exc.value.diagnostics) == 2


def test_missing_jsonschema_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real = builtins.__import__

    def no_jsonschema(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] == "jsonschema":
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_jsonschema)
    path = write(tmp_path, [{"type": "json_schema", "name": "a", "schema": {}}])
    with pytest.raises(InputError) as exc:
        load_evaluators_config(path)
    assert codes(exc) == {"evaluators_config"}
    assert "evalgate-ai[schema]" in exc.value.diagnostics[0].message


def test_yaml_errors(tmp_path: Path) -> None:
    path = tmp_path / "evaluators.yaml"
    path.write_text("version: 1\nversion: 1\n")
    with pytest.raises(InputError) as exc:
        load_evaluators_config(path)
    assert codes(exc) == {"evaluators_yaml"}
    path.write_text("evaluators: [unclosed\n")
    with pytest.raises(InputError) as exc:
        load_evaluators_config(path)
    assert codes(exc) == {"evaluators_yaml"}


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(InputError) as exc:
        load_evaluators_config(tmp_path / "nope.yaml")
    assert codes(exc) == {"file_missing"}


def test_absolute_schema_file_outside_is_rejected(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    sub = tmp_path / "cfg"
    sub.mkdir()
    path = write(sub, [{"type": "json_schema", "name": "a", "schema_file": str(outside)}])
    with pytest.raises(InputError) as exc:
        load_evaluators_config(path)
    assert codes(exc) == {"evaluators_config"}


def test_invalid_schema_message_is_short(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    path = write(tmp_path, [{"type": "json_schema", "name": "a", "schema": {"type": "nonsense"}}])
    with pytest.raises(InputError) as exc:
        load_evaluators_config(path)
    assert "\n" not in exc.value.diagnostics[0].message


HEADER = "version: 1\nmanifest: {dataset_id: d, dataset_version: v, evaluator_version: e}\n"


@pytest.mark.parametrize(
    "entry,ok",
    [
        ("{type: numeric_close, name: n, abs_tol: 1}", True),  # YAML int for a float: fine
        ("{type: contains, name: c, text: hi, ignore_case: yes}", True),  # YAML 1.1 bool
        ("{type: length, name: l, max: 3.0}", False),
        ("{type: length, name: l, max: true}", False),
        ("{type: contains, name: c, text: yes}", False),  # parsed as a bool, not text
        ("{type: exact_match, name: e, normalize: strip}", False),
        ("{type: numeric_close, name: n, abs_tol: .nan}", False),
    ],
)
def test_hand_written_yaml(tmp_path: Path, entry: str, ok: bool) -> None:
    path = tmp_path / "evaluators.yaml"
    path.write_text(HEADER + f"evaluators:\n  - {entry}\n")
    if ok:
        load_evaluators_config(path)
    else:
        with pytest.raises(InputError):
            load_evaluators_config(path)


def test_yaml_int_tolerance_matches_python_version(tmp_path: Path) -> None:
    path = tmp_path / "evaluators.yaml"
    path.write_text(HEADER + "evaluators:\n  - {type: numeric_close, name: n, abs_tol: 1}\n")
    (ev,) = load_evaluators_config(path).evaluators
    assert ev.version == numeric_close(name="n", abs_tol=1.0).version
