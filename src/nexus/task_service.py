"""User-owned task operations kept separate from external aggregators."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .models import Task
from .storage import load_tasks, save_tasks
from .utils import dedupe_tasks


def add_manual_task(
    data_dir: Path,
    *,
    title: str,
    due_at: datetime | None = None,
    course: str | None = None,
    priority: int = 1,
    tags: list[str] | None = None,
    notes: str | None = None,
    now: datetime | None = None,
) -> Task:
    """Create a local task and immediately merge it into the task snapshot."""
    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("Task title is required")
    if priority not in {0, 1, 2, 3}:
        raise ValueError("Priority must be between 0 and 3")

    created_at = now or datetime.now().astimezone()
    normalized_tags = [tag.strip() for tag in (tags or []) if tag.strip()]
    if "manual" not in normalized_tags:
        normalized_tags.insert(0, "manual")
    task = Task(
        id=f"manual:{uuid4().hex}",
        title=normalized_title,
        due_at=due_at,
        source="manual",
        course=course.strip() if course and course.strip() else None,
        priority=priority,
        tags=normalized_tags,
        snippet=notes.strip() if notes and notes.strip() else None,
        received_at=created_at,
    )
    path = Path(data_dir).expanduser() / "tasks.json"
    save_tasks(dedupe_tasks([*load_tasks(path), task]), path)
    return task
