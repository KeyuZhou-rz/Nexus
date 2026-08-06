from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
ALLOWED_DATA_FILES = {"data/feeds.example.json"}


def test_source_release_tracks_no_runtime_or_credential_artifacts() -> None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    tracked = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    violations = sorted(
        path
        for path in tracked
        if (
            (path.startswith("data/") and path not in ALLOWED_DATA_FILES)
            or "/__pycache__/" in path
            or path.endswith(".pyc")
            or path.endswith("/.DS_Store")
            or path == ".DS_Store"
            or "client_secret" in path.casefold()
            or path.casefold().endswith("token.json")
            or path == ".env"
        )
    )
    assert violations == [], "release contains local/private artifacts: " + ", ".join(violations)


def test_streamlit_usage_stats_are_disabled() -> None:
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["browser"]["gatherUsageStats"] is False
