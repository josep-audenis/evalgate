"""An example EvalGate reporter: the candidate's failing checks as CSV.

Install it next to EvalGate, then use it from `compare`:

    pip install -e examples/reporter-plugin
    evalgate reporters
    evalgate compare base cand --policy evalgate.yaml --format csv=failing.csv

A reporter only returns text (or bytes); EvalGate writes the file.
"""

import csv
import io

from evalgate import Report


def render(report: Report) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["status", "case_id", "check"])
    failing = report.comparison.candidate_failing_checks if report.comparison else []
    for c in failing:
        writer.writerow([report.status, c.case_id, c.check])
    if not failing:
        writer.writerow([report.status, "", ""])  # INVALID or nothing failing: still one row
    return out.getvalue()
