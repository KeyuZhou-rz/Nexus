"""Process-local registry for explicit SQLite and Chroma handles."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path


_LOCK = threading.RLock()
_ACTIVE: dict[tuple[str, Path], set[str]] = {}


def register_connection(kind: str, path: Path) -> str:
    token = uuid.uuid4().hex
    key = (kind, path.expanduser().resolve())
    with _LOCK:
        _ACTIVE.setdefault(key, set()).add(token)
    return token


def unregister_connection(kind: str, path: Path, token: str | None) -> None:
    if token is None:
        return
    key = (kind, path.expanduser().resolve())
    with _LOCK:
        tokens = _ACTIVE.get(key)
        if not tokens:
            return
        tokens.discard(token)
        if not tokens:
            _ACTIVE.pop(key, None)


def active_connection_counts(
    *, sqlite_path: Path, chroma_path: Path
) -> dict[str, int]:
    targets = {
        "sqlite": sqlite_path.expanduser().resolve(),
        "chroma": chroma_path.expanduser().resolve(),
    }
    with _LOCK:
        return {
            kind: len(_ACTIVE.get((kind, path), set()))
            for kind, path in targets.items()
        }
