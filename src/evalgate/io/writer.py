from pathlib import Path

from evalgate.models import Report


def write_json_report(report: Report, path: Path) -> None:
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
