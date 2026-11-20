"""Report plugins: `render(report) -> str | bytes` functions from installed packages.

Registered under the entry point group `evalgate.reporters` (docs/design/phase-7.md §6).
Listing reads metadata only; plugin code is imported only by `load`, which the CLI calls after
every decision and trusted write. A reporter runs in this process: it gets a deep copy of the
report, but deliberate interference (os._exit, monkeypatching) can't be prevented.
"""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata

from evalgate.models import Report

GROUP = "evalgate.reporters"

Reporter = Callable[[Report], object]


class ReporterError(ValueError):
    """An unknown or ambiguous reporter name, or a reporter result EvalGate can't write."""


@dataclass(frozen=True)
class ReporterInfo:
    name: str
    target: str  # "module:function", as registered; never imported to list it
    distribution: str | None
    version: str | None


def _entry_points() -> list[metadata.EntryPoint]:
    # entry_points() already keeps one copy of a distribution found twice on sys.path.
    return list(metadata.entry_points(group=GROUP))


def available() -> list[ReporterInfo]:
    """Every installed reporter, sorted; a name registered twice is listed twice."""
    infos = [
        ReporterInfo(
            name=ep.name,
            target=ep.value,
            distribution=ep.dist.name if ep.dist else None,
            version=ep.dist.version if ep.dist else None,
        )
        for ep in _entry_points()
    ]
    return sorted(infos, key=lambda i: (i.name, i.distribution or "", i.target))


def resolve(name: str) -> metadata.EntryPoint:
    """The one entry point named exactly `name`. Metadata only: nothing is imported."""
    eps = _entry_points()
    matches = [ep for ep in eps if ep.name == name]
    if not matches:
        names = ", ".join(sorted({ep.name for ep in eps})) or "none installed"
        raise ReporterError(f"no reporter named {name!r} (installed: {names})")
    if len(matches) > 1:
        owners = ", ".join(sorted(ep.dist.name if ep.dist else ep.value for ep in matches))
        raise ReporterError(f"reporter {name!r} is registered by more than one package: {owners}")
    return matches[0]


def load(entry_point: metadata.EntryPoint) -> Reporter:
    """Import the reporter. Its import-time code runs here."""
    fn = entry_point.load()
    if not callable(fn):
        raise ReporterError(f"reporter {entry_point.name!r} is not callable")
    return fn  # type: ignore[no-any-return]


def call(name: str, fn: Reporter, report: Report) -> str | bytes:
    """Run a reporter on a deep copy and check its result."""
    result = fn(report.model_copy(deep=True))
    if isinstance(result, str):
        try:
            result.encode("utf-8")
        except UnicodeEncodeError:
            raise ReporterError(f"reporter {name!r} returned text that isn't valid UTF-8") from None
        return result
    if isinstance(result, bytes):
        return result
    raise ReporterError(f"reporter {name!r} returned {type(result).__name__}, not str or bytes")


def as_bytes(result: str | bytes) -> bytes:
    """What gets written: UTF-8 with no newline translation, or the bytes unchanged."""
    return result.encode("utf-8") if isinstance(result, str) else result


def render(name: str, report: Report) -> str | bytes:
    """Load reporter `name` and render `report` with it. Raises ReporterError for an unknown or
    ambiguous name or a bad result; the reporter's own exceptions propagate unchanged."""
    return call(name, load(resolve(name)), report)
