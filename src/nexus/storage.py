from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import FeedSource, Project, Task

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"
TASKS_FILE = DATA_DIR / "tasks.json"
FEEDS_FILE = DATA_DIR / "feeds.json"


def load_projects(path: Path = PROJECTS_FILE) -> list[Project]:
    """Loads projects from JSON file."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Project.from_dict(item) for item in data]


def save_projects(projects: Iterable[Project], path: Path = PROJECTS_FILE) -> None:
    """Saves projects to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [project.to_dict() for project in projects]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_tasks(path: Path = TASKS_FILE) -> list[Task]:
    """Loads tasks from JSON file."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Task.from_dict(item) for item in data]


def save_tasks(tasks: Iterable[Task], path: Path = TASKS_FILE) -> None:
    """Saves tasks to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [task.to_dict() for task in tasks]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def tasks_last_updated(path: Path = TASKS_FILE) -> datetime | None:
    """Returns the last modification time of the tasks file."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone()


def load_feeds(path: Path = FEEDS_FILE) -> list[FeedSource]:
    """Loads feed configurations from JSON file."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [FeedSource.from_dict(item) for item in data]


def save_feeds(feeds: Iterable[FeedSource], path: Path = FEEDS_FILE) -> None:
    """Saves feed configurations to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [feed.to_dict() for feed in feeds]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
