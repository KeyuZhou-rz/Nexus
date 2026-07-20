from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from .schemas import TASK_SCHEMA_VERSION

TaskStatus = Literal["open", "in_progress", "done", "blocked"]
FeedKind = Literal["brightspace_ical", "brightspace_rss", "ical_file"]
FeedAudience = Literal["ui", "llm"]
FeedMode = Literal["default", "exams_only", "assignments_only"]


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
