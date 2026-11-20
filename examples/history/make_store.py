"""Build a small local history store from the quickstart runs, then read it back.

It records both quickstart runs as run entries (two releases of the same app on the same
dataset), their compare (FAIL), and the candidate again as a patch release that still fails the
same check, with fixed times so the store is the same on every machine.

Run from the repo root:  python examples/history/make_store.py
Then try:                evalgate history list --history examples/history/store
                         evalgate history trend scores.quality --history examples/history/store
                         evalgate history show 3 --history examples/history/store
                         evalgate history failures --history examples/history/store
It also writes history.html (open it in a browser), like `evalgate history report`.

The store isn't committed (`store/` is regenerated): it embeds the EvalGate version in its
stored report, so a committed copy would change with every release.
"""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import evalgate
from evalgate.reporting.history_html import render_history_html

HERE = Path(__file__).parent
QUICKSTART = HERE.parent / "quickstart"
STORE = HERE / "store"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

shutil.rmtree(STORE, ignore_errors=True)
evalgate.record_run(QUICKSTART / "baseline", STORE, label="v1.4.0", recorded_at=T0)
evalgate.record_run(
    QUICKSTART / "candidate", STORE, label="v1.5.0", recorded_at=T0 + timedelta(days=7)
)
report = evalgate.compare(
    QUICKSTART / "baseline", QUICKSTART / "candidate", QUICKSTART / "evalgate.yaml"
)
evalgate.record_report(report, STORE, label="v1.5.0 gate", recorded_at=T0 + timedelta(days=7))
# A patch release that didn't fix it: the same safety failure, two run entries in a row.
evalgate.record_run(
    QUICKSTART / "candidate", STORE, label="v1.5.1", recorded_at=T0 + timedelta(days=14)
)

# The same store as one page: a trend chart per metric, the entries and the recurring failures.
(HERE / "history.html").write_text(
    render_history_html(evalgate.read_history(STORE)), encoding="utf-8"
)

for entry in evalgate.read_history(STORE):
    status = getattr(entry, "status", "valid")
    print(f"{entry.id}  {entry.kind:<7}  {status:<7}  {entry.label}")
