"""S1a/S1b: stable obligation identity and SQLite upsert."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus.academic_state import (
    obligation_id_for_task,
    persist_obligations,
    recent_changes,
    refresh_from_snapshot,
    tasks_from_obligations,
)
from nexus.aggregators.brightspace import BrightspaceAggregator, FeedConfig
from nexus.io_utils import atomic_write_text
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.models import Task
from nexus.task_service import add_manual_task, mark_task_done, start_task


def _task(
    *,
    id_: str,
    title: str,
    due_at: datetime,
    course: str = "CS202_OS",
    source: str = "brightspace",
    tags: list[str] | None = None,
) -> Task:
    return Task(
        id=id_,
        title=title,
        due_at=due_at,
        source=source,
        course=course,
        tags=tags or ["assignment"],
    )


def test_due_date_change_keeps_same_obligation_id() -> None:
    friday = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    monday = friday + timedelta(days=3)
    first = _task(id_="bspace_ical:old", title="Assignment 4", due_at=friday)
    moved = _task(id_="bspace_ical:new", title="Assignment 4", due_at=monday)
    assert obligation_id_for_task(first) == obligation_id_for_task(moved)
    assert obligation_id_for_task(first)


def test_cross_source_same_work_shares_obligation_id() -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    ical = _task(id_="bspace_ical:a", title="Homework 1", due_at=due, source="brightspace")
    canvas = _task(id_="canvas:99", title="Homework 1", due_at=due, source="canvas")
    assert obligation_id_for_task(ical) == obligation_id_for_task(canvas)


def test_work_block_is_not_an_obligation() -> None:
    due = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)
    block = _task(
        id_="manual:wb",
        title="工作块：Lab",
        due_at=due,
        source="manual",
        tags=["work_block", "manual"],
    )
    assert obligation_id_for_task(block) is None


def test_persist_updates_due_on_same_row(tmp_path: Path) -> None:
    friday = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    monday = friday + timedelta(days=3)
    first = _task(id_="bspace_ical:old", title="Assignment 4", due_at=friday)
    persist_obligations(tmp_path, [first])
    key = obligation_id_for_task(first)
    assert key
    store = SQLiteStore(tmp_path / "nexus.db")
    assert store.get_obligation(key)["due_at"] == friday.isoformat()

    moved = _task(id_="bspace_ical:new", title="Assignment 4", due_at=monday)
    persist_obligations(tmp_path, [moved])
    rows = store.list_obligations()
    assert len(rows) == 1
    assert rows[0]["id"] == key
    assert rows[0]["due_at"] == monday.isoformat()
    assert "bspace_ical:new" in rows[0]["source_ids"]
    kinds = [item["kind"] for item in recent_changes(tmp_path, limit=10)]
    assert "due_changed" in kinds
    assert "created" in kinds


def test_persist_merges_cross_source_into_one_row(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    persist_obligations(
        tmp_path,
        [
            _task(id_="bspace_ical:a", title="Homework 1", due_at=due, source="brightspace"),
            _task(id_="canvas:99", title="Homework 1", due_at=due, source="canvas"),
        ],
    )
    rows = SQLiteStore(tmp_path / "nexus.db").list_obligations()
    assert len(rows) == 1
    ids = rows[0]["source_ids"]
    assert "bspace_ical:a" in ids
    assert "canvas:99" in ids
    assert rows[0]["primary_task_id"] == "bspace_ical:a"


def test_persist_skips_work_blocks(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)
    stats = persist_obligations(
        tmp_path,
        [
            _task(id_="manual:wb", title="工作块：Lab", due_at=due, source="manual", tags=["work_block"]),
        ],
    )
    assert stats["upserted"] == 0
    assert SQLiteStore(tmp_path / "nexus.db").list_obligations() == []


def test_ical_due_change_with_stable_uid_is_one_obligation(tmp_path: Path) -> None:
    ics_a = tmp_path / "week.ics"
    atomic_write_text(
        ics_a,
        "BEGIN:VCALENDAR\nVERSION:2.0\n"
        "BEGIN:VEVENT\nUID:asgn-4\n"
        "DTSTART:20260904T235900Z\n"
        "SUMMARY:Assignment 4\nEND:VEVENT\nEND:VCALENDAR\n",
    )
    first = BrightspaceAggregator(
        ical_feeds=[FeedConfig(name="CS", url=ics_a.resolve().as_uri(), course="CS")],
    ).fetch_tasks()
    persist_obligations(tmp_path, first)

    atomic_write_text(
        ics_a,
        "BEGIN:VCALENDAR\nVERSION:2.0\n"
        "BEGIN:VEVENT\nUID:asgn-4\n"
        "DTSTART:20260907T235900Z\n"
        "SUMMARY:Assignment 4\nEND:VEVENT\nEND:VCALENDAR\n",
    )
    moved = BrightspaceAggregator(
        ical_feeds=[FeedConfig(name="CS", url=ics_a.resolve().as_uri(), course="CS")],
    ).fetch_tasks()
    persist_obligations(tmp_path, moved)

    rows = SQLiteStore(tmp_path / "nexus.db").list_obligations()
    assert len(rows) == 1
    assert rows[0]["due_at"].startswith("2026-09-07")
    assert obligation_id_for_task(first[0]) == obligation_id_for_task(moved[0])
    assert any(item["kind"] == "due_changed" for item in recent_changes(tmp_path))


def test_reconcile_marks_disappeared_obligation(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    first = _task(id_="bspace_ical:keep", title="Assignment 4", due_at=due)
    other = _task(id_="bspace_ical:gone", title="Lab 2", due_at=due, tags=["lab"])
    persist_obligations(tmp_path, [first, other])
    persist_obligations(tmp_path, [first], reconcile_missing=True)
    rows = {row["title"]: row for row in SQLiteStore(tmp_path / "nexus.db").list_obligations()}
    assert rows["Lab 2"]["status"] == "missing"
    assert any(item["kind"] == "disappeared" for item in recent_changes(tmp_path))
    live = tasks_from_obligations(tmp_path, [first, other])
    assert all(task.title != "Lab 2" for task in live)


def test_mark_done_updates_obligation_status(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    task = add_manual_task(
        tmp_path,
        title="Homework 1",
        due_at=due,
        course="CS202_OS",
        tags=["assignment"],
    )
    persist_obligations(tmp_path, [task])
    mark_task_done(tmp_path, task.id)
    key = obligation_id_for_task(task)
    assert key
    row = SQLiteStore(tmp_path / "nexus.db").get_obligation(key)
    assert row is not None
    assert row["status"] == "done"
    assert float(row["remaining_hours"] or 0) == 0.0
    assert row["risk_band"] != "high"


def test_timed_work_updates_spent_and_remaining(tmp_path: Path) -> None:
    due = datetime(2026, 9, 10, 23, 59, tzinfo=timezone.utc)
    task = add_manual_task(
        tmp_path,
        title="Graphics project",
        due_at=due,
        course="Graphics",
        tags=["project"],
    )
    started = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    start_task(tmp_path, task.id, now=started)
    mark_task_done(tmp_path, task.id, now=started + timedelta(hours=2), effort_minutes=120)
    key = obligation_id_for_task(task)
    assert key
    row = SQLiteStore(tmp_path / "nexus.db").get_obligation(key)
    assert row is not None
    assert float(row["spent_hours"]) == 2.0
    assert float(row["remaining_hours"]) == 0.0
    assert row["status"] == "done"
    assert row["risk_band"] != "high"


def test_open_obligation_never_labelled_high_without_available_time(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    persist_obligations(
        tmp_path,
        [_task(id_="bspace_ical:p", title="Graphics project", due_at=due, course="Graphics", tags=["project"])],
    )
    row = SQLiteStore(tmp_path / "nexus.db").list_obligations()[0]
    assert float(row["estimated_hours"]) > 0
    assert row["risk_band"] in {"unscored", "overdue"}
    assert row["risk_band"] != "high"


def test_refresh_from_snapshot_writes_then_projects(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    homework = _task(id_="bspace_ical:hw", title="Homework 9", due_at=due)
    holiday = Task(
        id="gcal:holiday",
        title="Chinese Valentine's Day",
        due_at=due,
        source="gcal",
        course="Holidays in China",
        tags=["calendar"],
    )
    live = refresh_from_snapshot(tmp_path, [homework, holiday])
    assert [task.title for task in live] == ["Homework 9"]
    rows = SQLiteStore(tmp_path / "nexus.db").list_obligations()
    assert len(rows) == 1
    assert rows[0]["title"] == "Homework 9"
