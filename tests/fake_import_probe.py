"""A reporter module that records, at import time, which files already exist."""

import os

SEEN = sorted(os.listdir(os.environ["EVALGATE_PROBE_DIR"]))


def render(report: object) -> str:
    return "probe\n"
