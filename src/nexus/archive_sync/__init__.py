"""
nexus.archive_sync
==================
Playwright-based Brightspace scraper that logs in via NYU SSO (headed browser,
so the user can complete Duo MFA), then fetches Assignments, Calendar, and
Grades, converting each item to a Task and merging into tasks.json.

Public API
----------
run_archive_sync_subprocess() -> dict
    Launch the scraper as a subprocess (avoids asyncio event-loop conflicts
    with Streamlit) and return the parsed JSON result.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SRC_DIR = str(Path(__file__).resolve().parents[2])  # …/src/


def run_archive_sync_subprocess(timeout: int = 300) -> dict:
    """
    Run ``python -m nexus.archive_sync`` in a subprocess and return the
    JSON result dict.  Raises ``RuntimeError`` on non-zero exit or JSON
    parse failure.
    """
    env = {**os.environ, "PYTHONPATH": _SRC_DIR}
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.archive_sync"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if proc.stderr:
        # Forward debug output so it appears in the Streamlit terminal
        print(proc.stderr, file=sys.stderr, end="")

    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"archive_sync subprocess produced no output (exit {proc.returncode}).\n"
            f"stderr: {proc.stderr[:500]}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"archive_sync output is not valid JSON: {exc}\noutput: {stdout[:500]}")
