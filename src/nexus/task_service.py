"""User-owned task operations kept separate from external aggregators."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import ensure_private_runtime_dir
from .io_utils import atomic_write_json
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
    merged = dedupe_tasks([*load_tasks(path), task])
    save_tasks(merged, path)
    try:
        from .academic_state import persist_obligations

        persist_obligations(data_dir, merged)
    except Exception:
        pass
    return task


def _completions_path(data_dir: Path) -> Path:
    return Path(data_dir).expanduser() / "completions.json"


def completed_task_ids(completions: dict[str, dict] | None) -> set[str]:
    """Return ids that were actually finished, not merely started."""
    done: set[str] = set()
    for task_id, entry in (completions or {}).items():
        if isinstance(entry, dict) and entry.get("done_at"):
            done.add(str(task_id))
    return done


def load_completions(data_dir: Path) -> dict[str, dict]:
    """Return task-id → completion entry map ({'done_at': ISO, 'effort_minutes': int|None})."""
    path = _completions_path(data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_completions(data_dir: Path, completions: dict[str, dict]) -> None:
    path = _completions_path(data_dir)
    ensure_private_runtime_dir(path.parent)
    atomic_write_json(path, completions)


def start_task(
    data_dir: Path,
    task_id: str,
    now: datetime | None = None,
) -> dict:
    """Mark a task as started so completion can learn elapsed effort."""
    started_at = now or datetime.now().astimezone()
    completions = load_completions(data_dir)
    entry = completions.get(task_id)
    if not isinstance(entry, dict):
        entry = {}
    if entry.get("done_at"):
        return entry
    if not entry.get("started_at"):
        entry["started_at"] = started_at.isoformat()
    completions[task_id] = entry
    save_completions(data_dir, completions)
    return entry


def mark_task_done(
    data_dir: Path,
    task_id: str,
    now: datetime | None = None,
    effort_minutes: int | float | None = None,
) -> Task | None:
    """Record a completion and reflect it on tasks.json immediately.

    The durable record lives in completions.json (keyed by stable task id) so it
    survives re-aggregation; tasks.json is updated only for live dashboard reads.
    When the task was started, elapsed minutes are stored for later estimates.
    """
    done_at = now or datetime.now().astimezone()
    path = Path(data_dir).expanduser() / "tasks.json"
    tasks = load_tasks(path)
    matched: Task | None = None
    for task in tasks:
        if task.id == task_id:
            task.status = "done"
            task.done_at = done_at
            matched = task

    completions = load_completions(data_dir)
    entry = completions.get(task_id)
    if not isinstance(entry, dict):
        entry = {}
    minutes = effort_minutes
    if minutes is None:
        started_raw = entry.get("started_at")
        if started_raw:
            try:
                started = datetime.fromisoformat(str(started_raw))
                if started.tzinfo is None and done_at.tzinfo is not None:
                    started = started.replace(tzinfo=done_at.tzinfo)
                elapsed = (done_at - started).total_seconds() / 60.0
                if elapsed > 0:
                    minutes = round(elapsed)
            except ValueError:
                minutes = None
    if matched is not None:
        from .intelligence.workload import effort_key

        entry["effort_key"] = effort_key(matched)
        if matched.course:
            entry["course"] = matched.course
        if matched.due_at is not None:
            entry["due_at"] = matched.due_at.isoformat()
    entry["done_at"] = done_at.isoformat()
    entry["effort_minutes"] = minutes
    completions[task_id] = entry
    save_completions(data_dir, completions)

    if matched is not None:
        save_tasks(dedupe_tasks(tasks), path)
        try:
            from .academic_state import persist_obligations

            persist_obligations(data_dir, tasks)
        except Exception:
            pass
    return matched


def apply_completions(tasks: list[Task], completions: dict[str, dict]) -> list[Task]:
    """Re-apply persisted completion state onto freshly-aggregated tasks."""
    for task in tasks:
        entry = completions.get(task.id)
        if not isinstance(entry, dict) or not entry.get("done_at"):
            continue
        task.status = "done"
        done_at_raw = entry.get("done_at")
        try:
            task.done_at = datetime.fromisoformat(str(done_at_raw))
        except ValueError:
            task.done_at = None
    return tasks
