"""Deterministic in-memory builders for the V1 State → Action loop tests."""
from __future__ import annotations

from datetime import datetime, timedelta

from nexus.core.models import Task
from nexus.core.v1_config import V1_NOW, V1_TZ

CS_TITLE = "CS Assignment 2"
HISTORY_TITLE = "History Reading"
CS_COURSE = "CS101"
HISTORY_COURSE = "HIST101"


def v1_now() -> datetime:
    return V1_NOW


def cs_due_original() -> datetime:
    """Next Monday 09:00 local from V1_NOW."""
    return datetime(2026, 9, 14, 9, 0, tzinfo=V1_TZ)


def cs_due_moved() -> datetime:
    """Tuesday 09:00 local — 24 hours after V1_NOW."""
    return datetime(2026, 9, 8, 9, 0, tzinfo=V1_TZ)


def history_due() -> datetime:
    """Wednesday 09:00 local."""
    return datetime(2026, 9, 9, 9, 0, tzinfo=V1_TZ)


def cs_assignment_task(*, due_at: datetime | None = None, source: str = "manual") -> Task:
    return Task(
        id=f"{source}:cs-assignment-2",
        title=CS_TITLE,
        due_at=due_at or cs_due_original(),
        source=source,
        course=CS_COURSE,
        tags=["assignment", "manual"] if source == "manual" else ["assignment"],
    )


def history_reading_task() -> Task:
    return Task(
        id="manual:history-reading",
        title=HISTORY_TITLE,
        due_at=history_due(),
        source="manual",
        course=HISTORY_COURSE,
        tags=["reading", "manual"],
    )


def twelve_hour_rules() -> list[dict[str, object]]:
    """Recurring study windows totaling 12h between V1_NOW and next Monday 09:00.

    Mon 14:00–18:00 (4h remaining after 09:00), Tue–Fri 16:00–18:00 (2h × 4 = 8h).
    """
    return [
        {
            "weekday": 0,
            "starts_local": "14:00",
            "ends_local": "18:00",
            "timezone": "America/New_York",
            "enabled": True,
        },
        {
            "weekday": 1,
            "starts_local": "16:00",
            "ends_local": "18:00",
            "timezone": "America/New_York",
            "enabled": True,
        },
        {
            "weekday": 2,
            "starts_local": "16:00",
            "ends_local": "18:00",
            "timezone": "America/New_York",
            "enabled": True,
        },
        {
            "weekday": 3,
            "starts_local": "16:00",
            "ends_local": "18:00",
            "timezone": "America/New_York",
            "enabled": True,
        },
        {
            "weekday": 4,
            "starts_local": "16:00",
            "ends_local": "18:00",
            "timezone": "America/New_York",
            "enabled": True,
        },
    ]


def conflict_block_leaving_two_hours() -> dict[str, object]:
    """Occupies Monday 16:00–18:00 so only Mon 14:00–16:00 (2h) remains before Tuesday 09:00."""
    start = datetime(2026, 9, 7, 16, 0, tzinfo=V1_TZ)
    return {
        "source": "gcal",
        "external_id": "conflict-mon-16-18",
        "title": "Club meeting",
        "starts_at": start,
        "ends_at": start + timedelta(hours=2),
        "kind": "busy",
        "status": "confirmed",
    }
