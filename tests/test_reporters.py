# ruff: noqa: E501  (long fake entry-point tables)
"""Report plugin registry and SDK (phase-7 §6, 0.14.0 T1).

Distributions are faked through importlib.metadata.distributions, so entry_points() runs its own
lookup and deduplication; nothing is installed.
"""

import ast
import sys
from importlib import metadata
from pathlib import Path

import pytest
from typer.testing import CliRunner

import evalgate
from evalgate.cli import app
from evalgate.io.reporters import ReporterError, available, render, resolve
from evalgate.io.writer import _atomic_write_bytes

QS = Path(__file__).parent.parent / "examples" / "quickstart"
SRC = Path(evalgate.__file__).parent
runner = CliRunner()


class FakeDist(metadata.Distribution):
    def __init__(self, name: str, eps: dict[str, str], version: str = "1.0") -> None:
        lines = "".join(f"{k} = {v}\n" for k, v in eps.items())
        self._files = {
            "METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            "entry_points.txt": f"[evalgate.reporters]\n{lines}",
        }

    def read_text(self, filename: str) -> str | None:
        return self._files.get(filename)

    def locate_file(self, path: object) -> Path:
        return Path(str(path))


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def install(*dists: FakeDist) -> None:
        monkeypatch.setattr(metadata, "distributions", lambda **kw: iter(dists))

    return install


def report() -> evalgate.Report:
    return evalgate.compare(QS / "baseline", QS / "candidate", QS / "evalgate.yaml")


def test_available_lists_without_importing(installed) -> None:  # type: ignore[no-untyped-def]
    sys.modules.pop("never_imported_module", None)
    installed(
        FakeDist("pkg-b", {"zeta": "never_imported_module:fn"}, "2.0"),
        FakeDist("pkg-a", {"csv": "fake_reporters:text"}),
    )
    infos = available()
    assert [(i.name, i.distribution, i.version, i.target) for i in infos] == [
        ("csv", "pkg-a", "1.0", "fake_reporters:text"),
        ("zeta", "pkg-b", "2.0", "never_imported_module:fn"),
    ]
    assert "never_imported_module" not in sys.modules


def test_duplicate_name_is_listed_twice_and_ambiguous(installed) -> None:  # type: ignore[no-untyped-def]
    installed(
        FakeDist("pkg-a", {"csv": "fake_reporters:text"}),
        FakeDist("pkg-b", {"csv": "fake_reporters:raw"}),
    )
    assert [i.distribution for i in available()] == ["pkg-a", "pkg-b"]
    with pytest.raises(ReporterError, match="pkg-a, pkg-b"):
        resolve("csv")


def test_same_distribution_twice_is_not_ambiguous(installed) -> None:  # type: ignore[no-untyped-def]
    installed(
        FakeDist("pkg-a", {"csv": "fake_reporters:text"}),
        FakeDist("pkg_a", {"csv": "fake_reporters:raw"}),
    )
    assert resolve("csv").value == "fake_reporters:text"  # first on the path wins


def test_unknown_name_lists_installed_and_is_case_sensitive(installed) -> None:  # type: ignore[no-untyped-def]
    installed(FakeDist("pkg-a", {"csv": "fake_reporters:text", "json2": "fake_reporters:raw"}))
    with pytest.raises(ReporterError, match="installed: csv, json2"):
        resolve("CSV")


def test_render_text_bytes_and_a_deep_copy(installed) -> None:  # type: ignore[no-untyped-def]
    installed(
        FakeDist(
            "pkg-a",
            {
                "text": "fake_reporters:text",
                "raw": "fake_reporters:raw",
                "mut": "fake_reporters:mutating",
            },
        )
    )
    r = report()
    assert render("text", r) == "status=FAIL\r\nline 2\n"
    assert render("raw", r) == b"\x00\xffFAIL"
    assert render("mut", r) == "mutated" and r.status == "FAIL"  # the caller's report is untouched
    assert r.comparison is not None and r.comparison.metrics and r.comparison.newly_failing_checks


@pytest.mark.parametrize(
    "target",
    ["fake_reporters:wrong_type", "fake_reporters:bad_text", "fake_reporters:not_callable"],
)
def test_bad_results_raise_reporter_error(installed, target: str) -> None:  # type: ignore[no-untyped-def]
    installed(FakeDist("pkg-a", {"bad": target}))
    with pytest.raises(ReporterError):
        render("bad", report())


def test_reporter_exceptions_propagate_in_the_sdk(installed) -> None:  # type: ignore[no-untyped-def]
    installed(FakeDist("pkg-a", {"boom": "no_such_module_xyz:fn"}))
    with pytest.raises(ModuleNotFoundError):
        render("boom", report())


def test_bytes_writer(tmp_path: Path) -> None:
    out = tmp_path / "x.bin"
    _atomic_write_bytes(out, b"old")
    _atomic_write_bytes(out, b"a\r\nb\x00")
    assert out.read_bytes() == b"a\r\nb\x00"  # an existing target is replaced
    (tmp_path / "d").mkdir()
    with pytest.raises(OSError):
        _atomic_write_bytes(tmp_path / "d", b"x")
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))


def test_cli_lists_reporters_escaped(installed) -> None:  # type: ignore[no-untyped-def]
    installed(FakeDist("pkg-[red]a", {"[bold]csv": "fake_reporters:text"}))
    result = runner.invoke(app, ["reporters"])
    assert result.exit_code == 0
    assert "[bold]csv" in result.output and "pkg-[red]a" in result.output


def test_cli_listing_shows_control_characters_escaped(installed) -> None:  # type: ignore[no-untyped-def]
    installed(
        FakeDist("pkg\x1b[31mred", {"ev\x1b]0;title\x07il": "fake_reporters:text"}, "1\x1b[2J")
    )
    result = runner.invoke(app, ["reporters"])
    assert result.exit_code == 0 and "\x1b" not in result.output and "\x07" not in result.output


def test_cli_with_nothing_installed(installed) -> None:  # type: ignore[no-untyped-def]
    installed()
    result = runner.invoke(app, ["reporters"])
    assert result.exit_code == 0 and "no reporters installed" in result.output


ENGINE = [
    SRC / "engine.py",
    SRC / "api.py",
    SRC / "validation.py",
    *(SRC / "comparison").glob("*.py"),
    *(SRC / "policies").glob("*.py"),
]


@pytest.mark.parametrize("path", ENGINE, ids=lambda p: p.name)
def test_engine_never_imports_reporters(path: Path) -> None:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {f"{node.module}.{a.name}" for a in node.names} | {node.module or ""}
    assert not any("reporters" in n or "importlib" in n for n in names)
