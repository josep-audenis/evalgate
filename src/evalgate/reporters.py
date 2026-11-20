"""Report plugins: list installed reporters and render a report with one.

See docs/design/phase-7.md section 6. Reporters are functions `render(report) -> str | bytes`
registered by installed packages under the entry point group `evalgate.reporters`.
"""

from evalgate.io.reporters import ReporterError, ReporterInfo, available, render

__all__ = ["ReporterError", "ReporterInfo", "available", "render"]
