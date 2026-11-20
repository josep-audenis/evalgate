"""Reporter functions for tests (registered through fake distributions, never installed)."""

from evalgate.models import Report

CALLS: list[str] = []


def text(report: Report) -> str:
    CALLS.append("text")
    return f"status={report.status}\r\nline 2\n"


def raw(report: Report) -> bytes:
    return b"\x00\xff" + report.status.encode()


def mutating(report: Report) -> str:
    report.status = "PASS"
    if report.comparison is not None:  # nested: only a deep copy protects the caller
        report.comparison.metrics.clear()
        report.comparison.newly_failing_checks.clear()
    return "mutated"


def wrong_type(report: Report) -> object:
    return None


def bad_text(report: Report) -> str:
    return "\udcff"


not_callable = "just a string"


def raise_value(report: Report) -> str:
    raise ValueError(f"SECRET-{report.status}")


def raise_exit(report: Report) -> str:
    raise SystemExit(0)


def raise_interrupt(report: Report) -> str:
    raise KeyboardInterrupt


def status_line(report: Report) -> str:
    return f"{report.status} comparison={report.comparison is not None}\n"
