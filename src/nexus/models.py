from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from .schemas import TASK_SCHEMA_VERSION

ProjectIDE = Literal["vscode", "unity", "pycharm", "other"]
TaskStatus = Literal["open", "in_progress", "done", "blocked"]
FeedKind = Literal["brightspace_ical", "brightspace_rss", "ical_file"]
FeedAudience = Literal["ui", "llm"]
FeedMode = Literal["default", "exams_only", "assignments_only"]


@dataclass
class Project:
    """Represents a coding project (for the 'Projects' dashboard)."""

    name: str
    path: str
    ide: ProjectIDE = "vscode"
    docs: list[str] = field(default_factory=list)
    launch: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Converts object to dictionary for JSON saving."""
        return {
            "name": self.name,
            "path": self.path,
            "ide": self.ide,
            "docs": self.docs,
            "launch": self.launch,
            "links": self.links,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        """Creates object from dictionary (JSON loading)."""
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            ide=data.get("ide", "vscode"),
            docs=list(data.get("docs", [])),
            launch=list(data.get("launch", [])),
            links=list(data.get("links", [])),
            notes=data.get("notes"),
        )


@dataclass
class Task:
    """A unified task object (from Email, Calendar, or Brightspace)."""

    id: str
    title: str
    due_at: datetime | None
    source: str
    url: str | None = None
    course: str | None = None
    status: TaskStatus = "open"
    priority: int = 0
    tags: list[str] = field(default_factory=list)
    snippet: str | None = None
    received_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serializes task to dictionary."""
        return {
            "schema_version": TASK_SCHEMA_VERSION,
            "id": self.id,
            "title": self.title,
            "snippet": self.snippet,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "source": self.source,
            "url": self.url,
            "course": self.course,
            "status": self.status,
            "priority": self.priority,
            "tags": self.tags,
            "received_at": self.received_at.isoformat() if self.received_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        """Deserializes task from dictionary."""
        due_at_raw = data.get("due_at")
        due_at = datetime.fromisoformat(due_at_raw) if due_at_raw else None
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            snippet=data.get("snippet"),
            due_at=due_at,
            source=str(data.get("source", "")),
            url=data.get("url"),
            course=data.get("course"),
            status=data.get("status", "open"),
            priority=int(data.get("priority", 0)),
            tags=list(data.get("tags", [])),
            received_at=datetime.fromisoformat(data["received_at"])
            if data.get("received_at")
            else None,
        )


@dataclass
class FeedSource:
    """Configuration for a data feed (stored in feeds.json)."""
    kind: FeedKind
    name: str
    url: str
    course: str | None = None
    enabled: bool = True
    audience: FeedAudience = "ui"
    mode: FeedMode = "default"

    def to_dict(self) -> dict[str, Any]:
        """Serializes feed config."""
        return {
            "kind": self.kind,
            "name": self.name,
            "url": self.url,
            "course": self.course,
            "enabled": self.enabled,
            "audience": self.audience,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeedSource":
        """Deserializes feed config."""
        return cls(
            kind=data.get("kind", "brightspace_ical"),
            name=str(data.get("name", "")),
            url=str(data.get("url", "")),
            course=data.get("course"),
            enabled=bool(data.get("enabled", True)),
            audience=data.get("audience", "ui"),
            mode=data.get("mode", "default"),
        )
