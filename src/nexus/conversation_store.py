from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _session_path(conversations_dir: Path, session_id: str) -> Path:
    return conversations_dir / f"{session_id}.jsonl"


def append_message(
    conversations_dir: Path,
    session_id: str,
    *,
    role: str,
    content: str,
    source: str = "cli",
    timestamp: str | None = None,
) -> str:
    conversations_dir.mkdir(parents=True, exist_ok=True)
    message_id = f"msg_{uuid4().hex[:12]}"
    payload: dict[str, Any] = {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": timestamp or datetime.now().astimezone().isoformat(),
        "source": source,
    }
    path = _session_path(conversations_dir, session_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return message_id


def load_messages(conversations_dir: Path, session_id: str, from_index: int = 0) -> list[dict[str, Any]]:
    path = _session_path(conversations_dir, session_id)
    if not path.exists():
        return []

    messages: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            if idx < from_index:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
    return messages
