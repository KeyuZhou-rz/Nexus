"""Cookie/session persistence for Brightspace Playwright sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path

from nexus.io_utils import atomic_write_json

DEFAULT_SESSION_PATH = Path(__file__).resolve().parents[3] / "data" / "brightspace_session.json"


def _resolve_path(path: Path | None) -> Path:
    if path is not None:
        return path
    data_dir = os.environ.get("NEXUS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "brightspace_session.json"
    return DEFAULT_SESSION_PATH


def load_session(path: Path | None = None) -> list[dict]:
    """Load saved browser cookies from disk. Returns empty list if file missing."""
    path = _resolve_path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_session(cookies: list[dict], path: Path | None = None) -> None:
    """Persist browser cookies to disk for future sessions."""
    path = _resolve_path(path)
    atomic_write_json(path, cookies)
    if os.name == "posix":
        path.chmod(0o600)
