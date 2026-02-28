from __future__ import annotations

from datetime import datetime
from typing import Any

TASK_SCHEMA_VERSION = "1.1"
ARCHIVE_RESULT_SCHEMA_VERSION = "1.0"


_VALID_TASK_STATUS = {"open", "in_progress", "done", "blocked"}


def validate_task_dict(payload: dict[str, Any]) -> list[str]:
    """Lightweight runtime validation for Task payloads."""
    errors: list[str] = []

    task_id = str(payload.get("id", "")).strip()
    title = str(payload.get("title", "")).strip()
    source = str(payload.get("source", "")).strip()
    status = str(payload.get("status", "open")).strip()

    if not task_id:
        errors.append("id is required")
    if not title:
        errors.append("title is required")
    if not source:
        errors.append("source is required")
    if status not in _VALID_TASK_STATUS:
        errors.append(f"invalid status: {status}")

    due_at = payload.get("due_at")
    if due_at is not None:
        try:
            datetime.fromisoformat(str(due_at))
        except ValueError:
            errors.append("due_at must be ISO-8601 or null")

    received_at = payload.get("received_at")
    if received_at is not None:
        try:
            datetime.fromisoformat(str(received_at))
        except ValueError:
            errors.append("received_at must be ISO-8601 or null")

    tags = payload.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        errors.append("tags must be a list[str]")

    return errors
