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
        external_id_stable=True,
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
    from .academic_state import persist_obligations

    persist_obligations(data_dir, merged)
    save_tasks(merged, path)
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


def _canonical_id(data_dir: Path, task_id: str) -> str | None:
    from .academic_state_service import AcademicStateService

    service = AcademicStateService(data_dir)
    row = service._external_index().get(task_id)
    if row is None:
        service.ingest_snapshot(load_tasks(Path(data_dir) / "tasks.json"))
        row = service._external_index().get(task_id)
    return row["id"] if row else None


def start_task(data_dir: Path, task_id: str, now: datetime | None = None) -> dict:
    """Compatibility entrypoint for obligation-based timing."""
    from .action_service import ActionService

    oid = _canonical_id(data_dir, task_id)
    if oid:
        ActionService(data_dir).start(oid, now=now)
    return load_completions(data_dir).get(task_id, {})


def mark_task_done(
    data_dir: Path,
    task_id: str,
    now: datetime | None = None,
    effort_minutes: int | float | None = None,
) -> Task | None:
    """Compatibility entrypoint for atomic canonical completion."""
    from .action_service import ActionService

    oid = _canonical_id(data_dir, task_id)
    if oid:
        ActionService(data_dir).complete(oid, now=now, effort_minutes=effort_minutes)
    return next(
        (
            task
            for task in load_tasks(Path(data_dir) / "tasks.json")
            if task.id == task_id
        ),
        None,
    )


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
