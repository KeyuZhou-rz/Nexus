"""
LMS Base Interface — Nexus Phase X1

Unified abstraction over LMS backends (Brightspace, Canvas, Moodle, etc.).
Each backend implements courses / assignments / announcements / files
and can convert all data to Nexus Task objects.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..models import Task


# ── Domain models ────────────────────────────────────────────────────

@dataclass
class Course:
    """A course/enrollment record from the LMS."""
    course_id: str
    name: str
    term: str | None = None
    url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assignment:
    """An assignment with due date from the LMS."""
    assignment_id: str
    title: str
    course_id: str
    due_at: datetime | None = None
    url: str | None = None
    points_possible: float | None = None
    description: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Announcement:
    """A course announcement / news item from the LMS."""
    announcement_id: str
    title: str
    course_id: str
    body: str | None = None
    posted_at: datetime | None = None
    url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CourseFile:
    """A downloadable file resource from the LMS."""
    file_id: str
    name: str
    course_id: str
    url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# ── Connector interface ──────────────────────────────────────────────

class LMSConnector(ABC):
    """
    Unified LMS integration interface.

    Each concrete implementation (Brightspace, Canvas, etc.) provides
    the four abstract methods below.
    """

    name: str = "lms"

    # ── abstract ─────────────────────────────────────────────────

    @abstractmethod
    def list_courses(self) -> list[Course]:
        """List courses the authenticated user is enrolled in."""
        ...

    @abstractmethod
    def list_assignments(self, course_id: str) -> list[Assignment]:
        """List upcoming/submitted assignments for a course."""
        ...

    @abstractmethod
    def list_announcements(self, course_id: str) -> list[Announcement]:
        """List recent announcements for a course."""
        ...

    @abstractmethod
    def list_files(self, course_id: str) -> list[CourseFile]:
        """List downloadable files/modules for a course."""
        ...

    # ── concrete: convert to Nexus Tasks ──────────────────────────

    def to_tasks(self, course_id: str | None = None) -> list[Task]:
        """
        Convert all LMS items for one or all courses into unified Task objects.

        Assignments → tasks with due dates and 'assignment' tag.
        Announcements → tasks with 'announcement' tag.
        Files → tasks with 'resource' tag (no due date).
        """
        courses = [course_id] if course_id else [c.course_id for c in self.list_courses()]
        tasks: list[Task] = []

        for cid in courses:
            # Assignments → deadline tasks
            for a in self.list_assignments(cid):
                tags = ["assignment", self.name]
                title_lower = a.title.lower()
                if any(kw in title_lower for kw in ("exam", "midterm", "final", "quiz")):
                    tags.append("exam")
                tasks.append(Task(
                    id=f"{self.name}:assign:{cid}:{a.assignment_id}",
                    title=a.title,
                    due_at=a.due_at,
                    source=self.name,
                    url=a.url,
                    course=cid,
                    status="open",
                    priority=2,
                    tags=tags,
                    snippet=a.description[:200] if a.description else None,
                    received_at=datetime.now().astimezone(),
                ))

            # Announcements → info tasks
            for ann in self.list_announcements(cid):
                tasks.append(Task(
                    id=f"{self.name}:announce:{cid}:{ann.announcement_id}",
                    title=ann.title,
                    due_at=None,
                    source=self.name,
                    url=ann.url,
                    course=cid,
                    status="open",
                    priority=0,
                    tags=["announcement", self.name],
                    snippet=ann.body[:200] if ann.body else None,
                    received_at=ann.posted_at,
                ))

            # Files → resource tasks
            for f in self.list_files(cid):
                tasks.append(Task(
                    id=f"{self.name}:file:{cid}:{f.file_id}",
                    title=f.name,
                    due_at=None,
                    source=self.name,
                    url=f.url,
                    course=cid,
                    status="open",
                    priority=0,
                    tags=["resource", self.name],
                    snippet=f"{f.content_type or 'unknown'} ({_format_bytes(f.size_bytes)})",
                    received_at=datetime.now().astimezone(),
                ))

        return tasks


def _format_bytes(size: int | None) -> str:
    if size is None:
        return "unknown size"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
