"""Per-course academic state — how this course is doing right now.

One row per course that still has open obligation-candidates. Risk is only
``overdue`` or ``unscored``; never HIGH without available-time input.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .academic_state import _spent_hours, is_obligation_candidate
from .intelligence.workload import _pace_line, resolve_effort
from .models import Task
from .utils import localize_dt

UNASSIGNED_COURSE = "未分课程"


@dataclass
class CourseState:
    course: str
    open_count: int
    remaining_hours: float
    next_due_at: datetime | None
    next_title: str | None
    pace: str | None
    risk_band: str
    overdue_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "course": self.course,
            "open_count": self.open_count,
            "remaining_hours": self.remaining_hours,
            "next_due_at": self.next_due_at.isoformat() if self.next_due_at else None,
            "next_title": self.next_title,
            "pace": self.pace,
            "risk_band": self.risk_band,
            "overdue_count": self.overdue_count,
        }


def build_course_states(
    tasks: list[Task],
    *,
    completions: dict | None = None,
    now: datetime | None = None,
) -> list[CourseState]:
    """One row per course that still has open obligation-candidates, sorted by next due then remaining hours."""
    now_local = now or datetime.now().astimezone()
    grouped: dict[str, list[Task]] = {}
    for task in tasks:
        if getattr(task, "status", "open") == "done":
            continue
        if not is_obligation_candidate(task):
            continue
        grouped.setdefault(_course_label(task), []).append(task)

    states = [
        _state_for_course(course, items, tasks, completions, now_local)
        for course, items in grouped.items()
    ]
    states.sort(
        key=lambda state: (
            state.next_due_at is None,
            state.next_due_at or now_local,
            state.remaining_hours,
            state.course,
        )
    )
    return states


def _course_label(task: Task) -> str:
    return (task.course or "").strip() or UNASSIGNED_COURSE


def _state_for_course(
    course: str,
    items: list[Task],
    all_tasks: list[Task],
    completions: dict | None,
    now_local: datetime,
) -> CourseState:
    remaining = 0.0
    overdue_count = 0
    next_task: Task | None = None
    next_due: datetime | None = None
    for task in items:
        remaining += _remaining_hours(task, completions, now_local)
        due = localize_dt(task.due_at, now_local)
        if due is not None and due < now_local:
            overdue_count += 1
        if due is None:
            continue
        if next_due is None or due < next_due:
            next_due = due
            next_task = task

    representative = next_task or items[0]
    next_title = (representative.title or "").strip() or None if next_task else None
    # No available-time input: never label HIGH.
    risk_band = "overdue" if overdue_count else "unscored"
    pace = _pace_line(representative, all_tasks, completions, now_local)
    return CourseState(
        course=course,
        open_count=len(items),
        remaining_hours=round(remaining, 3),
        next_due_at=next_due,
        next_title=next_title,
        pace=pace,
        risk_band=risk_band,
        overdue_count=overdue_count,
    )


def _remaining_hours(
    task: Task,
    completions: dict[str, Any] | None,
    now_local: datetime,
) -> float:
    if task.remaining_hours is not None:
        return max(task.remaining_hours, 0.0)
    estimated, _basis = resolve_effort(task, completions)
    spent = _spent_hours([task.id], completions or {}, now_local)
    return max(estimated - spent, 0.0)
