from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import default_config
from .io_utils import atomic_write_json
from .models import FeedSource, Task
from .schemas import validate_task_dict

def _tasks_path(path: Path | None) -> Path:
    return path if path is not None else default_config().data_dir / "tasks.json"


def _feeds_path(path: Path | None) -> Path:
    return path if path is not None else default_config().data_dir / "feeds.json"


def load_tasks(path: Path | None = None) -> list[Task]:
    """Loads tasks from JSON file."""
    path = _tasks_path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Task.from_dict(item) for item in data]


def save_tasks(tasks: Iterable[Task], path: Path | None = None) -> None:
    """Saves tasks to JSON file."""
    path = _tasks_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [task.to_dict() for task in tasks]
    for item in payload:
        errors = validate_task_dict(item)
        if errors:
            raise ValueError(f"Invalid task payload for id={item.get('id')!r}: {', '.join(errors)}")
    atomic_write_json(path, payload)


def tasks_last_updated(path: Path | None = None) -> datetime | None:
    """Returns the last modification time of the tasks file."""
    path = _tasks_path(path)
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()


def load_feeds(path: Path | None = None) -> list[FeedSource]:
    """Loads feed configurations from JSON file."""
    path = _feeds_path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [FeedSource.from_dict(item) for item in data]


def save_feeds(feeds: Iterable[FeedSource], path: Path | None = None) -> None:
    """Saves feed configurations to JSON file."""
    path = _feeds_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [feed.to_dict() for feed in feeds]
    atomic_write_json(path, payload)
