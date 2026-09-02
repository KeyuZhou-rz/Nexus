"""S1a/S1b: stable obligation identity and SQLite upsert."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus.academic_state import (
    display_course,
    obligation_id_for_task,
    persist_obligations,
    recent_changes,
    refresh_from_snapshot,
    tasks_from_obligations,
)
from nexus.academic_state_service import AcademicStateService
from nexus.aggregators.brightspace import BrightspaceAggregator, FeedConfig
from nexus.io_utils import atomic_write_text
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.models import Task
from nexus.task_service import add_manual_task, mark_task_done, save_completions, start_task


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
    assert float(row["remaining_hours"]) > 0
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
    assert live[0].remaining_hours is not None
    assert live[0].risk_band in {"unscored", "overdue"}


def test_display_course_shortens_lms_dump() -> None:
    raw = "CAREER DEVELOPMENT ONLINE COURSE, 741-50768-7553-CAREER DEVELOPM, _Project Sites"
    assert display_course(raw) == "CAREER DEVELOPMENT ONLINE COURSE"
    civic = "Fall 2025 Civic Education 2 -《马克思主义：理论、方法与德性》, 190-420694-7553-Fall 2025 Civic"
    assert "马克思主义" in display_course(civic)
    assert "," not in display_course(civic)


def test_due_change_survives_reopen_and_records_both_change_logs(tmp_path: Path) -> None:
    friday = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    monday = friday + timedelta(days=3)
    first = _task(id_="bspace_ical:old", title="Assignment 4", due_at=friday)
    persist_obligations(tmp_path, [first])
    key = obligation_id_for_task(first)
    assert key
    moved = _task(id_="bspace_ical:new", title="Assignment 4", due_at=monday)
    persist_obligations(tmp_path, [moved])

    store = SQLiteStore(tmp_path / "nexus.db")
    row = store.get_obligation(key)
    assert row is not None
    assert row["id"] == key
    assert row["due_at"] == monday.isoformat()
    due_changes = [
        item for item in store.list_obligation_changes(limit=50) if item["kind"] == "due_changed"
    ]
    state_changes = store.list_state_changes(entity_id=key, kind="due_changed")
    assert len(due_changes) == 1
    assert len(state_changes) == 1
    before = json.loads(state_changes[0]["before_json"])
    after = json.loads(state_changes[0]["after_json"])
    assert before["due_at"] == friday.isoformat()
    assert after["due_at"] == monday.isoformat()


def test_cross_source_merge_writes_obligation_sources(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    persist_obligations(
        tmp_path,
        [
            _task(id_="bspace_ical:a", title="Homework 1", due_at=due, source="brightspace"),
            _task(id_="canvas:99", title="Homework 1", due_at=due, source="canvas"),
        ],
    )
    store = SQLiteStore(tmp_path / "nexus.db")
    rows = store.list_obligations()
    assert len(rows) == 1
    sources = store.list_obligation_sources(rows[0]["id"])
    assert len(sources) == 2
    external = {item["external_id"] for item in sources}
    assert external == {"bspace_ical:a", "canvas:99"}
    primaries = [item for item in sources if item["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["external_id"] == "bspace_ical:a"


def test_load_state_returns_open_obligations_without_tasks_json(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    persist_obligations(tmp_path, [_task(id_="bspace_ical:a", title="Assignment 4", due_at=due)])
    tasks_path = tmp_path / "tasks.json"
    if tasks_path.exists():
        tasks_path.unlink()
    state = AcademicStateService(tmp_path).load_state()
    titles = [row["title"] for row in state["obligations"]]
    assert "Assignment 4" in titles
    assert any(row.get("status") == "open" for row in state["obligations"])


def test_import_legacy_completions_is_idempotent(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    task = add_manual_task(
        tmp_path,
        title="Homework 1",
        due_at=due,
        course="CS202_OS",
        tags=["assignment"],
    )
    persist_obligations(tmp_path, [task])
    save_completions(
        tmp_path,
        {task.id: {"done_at": "2026-09-02T12:00:00+00:00", "effort_minutes": 45}},
    )
    service = AcademicStateService(tmp_path)
    first = service.import_legacy_completions()
    second = service.import_legacy_completions()
    assert first["events"] >= 1
    assert first["samples"] >= 1
    samples = service.store.list_effort_samples()
    completed = [
        event for event in service.store.list_action_events() if event["event_type"] == "completed"
    ]
    assert len(samples) == 1
    assert len(completed) == 1
    assert second["events"] == 0
    assert second["samples"] == 0


def test_add_manual_obligation_does_not_require_priority(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    row = AcademicStateService(tmp_path).add_manual_obligation(
        title="Essay",
        course="HIST101",
        due_at=due,
        estimated_hours=2.0,
    )
    assert row["id"].startswith("ob:")
    assert row["title"] == "Essay"
    assert row["course"] == "HIST101"


def test_progress_update_writes_progress_changed_and_override(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    service = AcademicStateService(tmp_path)
    row = service.add_manual_obligation(title="Lab report", course="CS", due_at=due)
    updated = service.update_obligation(
        row["id"], progress_percent=50, remaining_hours=2.0
    )
    assert updated["progress_percent"] == 50
    assert float(updated["remaining_override_hours"]) == 2.0
    assert float(updated["remaining_hours"]) == 2.0
    changes = service.store.list_state_changes(entity_id=row["id"], kind="progress_changed")
    assert len(changes) == 1


def test_calendar_block_upsert_same_source_external_id_is_one_row(tmp_path: Path) -> None:
    service = AcademicStateService(tmp_path)
    starts = datetime(2026, 9, 7, 17, 0, tzinfo=timezone.utc)
    ends = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)
    first = service.upsert_calendar_block(
        source="gcal",
        external_id="evt-1",
        title="Class",
        starts_at=starts,
        ends_at=ends,
    )
    second = service.upsert_calendar_block(
        source="gcal",
        external_id="evt-1",
        title="Class",
        starts_at=starts,
        ends_at=ends,
    )
    blocks = service.store.list_calendar_blocks()
    assert len(blocks) == 1
    assert first["id"] == second["id"]
    assert first["id"].startswith("cal:")
