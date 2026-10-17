"""Local log of recommendations the student saw, so ``nexus week`` can measure them."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ensure_private_runtime_dir

SHOWN_LOG = "shown.jsonl"
DEDUPE_WINDOW = timedelta(minutes=30)


def _parse(stamp: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp))
    except ValueError:
        return None


def load_shown(data_dir: Path, *, since: datetime | None = None) -> list[dict[str, Any]]:
    path = Path(data_dir).expanduser() / SHOWN_LOG
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        at = _parse(entry.get("at")) if isinstance(entry, dict) else None
        if at is None or (since is not None and at < since):
            continue
        entries.append({**entry, "at": at})
    return entries


def record_shown(
    data_dir: Path, obligation_id: str, *, surface: str, now: datetime | None = None
) -> bool:
    """Append one impression unless the same task was logged within the last 30 minutes."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    root = ensure_private_runtime_dir(Path(data_dir))
    recent = load_shown(root, since=current - DEDUPE_WINDOW)
    if recent and recent[-1].get("obligation_id") == obligation_id:
        return False
    line = json.dumps(
        {"at": current.isoformat(), "obligation_id": obligation_id, "surface": surface},
        ensure_ascii=False,
    )
    fd = os.open(root / SHOWN_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return True
