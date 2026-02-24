"""Cookie/session persistence for Brightspace Playwright sessions."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SESSION_PATH = Path(__file__).resolve().parents[3] / "data" / "brightspace_session.json"


def load_session(path: Path = DEFAULT_SESSION_PATH) -> list[dict]:
    """Load saved browser cookies from disk. Returns empty list if file missing."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_session(cookies: list[dict], path: Path = DEFAULT_SESSION_PATH) -> None:
    """Persist browser cookies to disk for future sessions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
