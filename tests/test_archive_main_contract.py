from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_archive_sync_missing_env_contract():
    repo_root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_BRIGHTSPACE_")}
    env["PYTHONPATH"] = "src"
    proc = subprocess.run(
        [sys.executable, "-m", "nexus.archive_sync"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    payload = json.loads(proc.stdout.strip())
    assert payload["status"] == "error"
    assert payload["schema_version"] == "1.0"
    assert "data" in payload
