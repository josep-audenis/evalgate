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
    return "mutated"


def wrong_type(report: Report) -> object:
    return None


def bad_text(report: Report) -> str:
    return "\udcff"


not_callable = "just a string"
