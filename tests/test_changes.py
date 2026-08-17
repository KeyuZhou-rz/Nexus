"""Inbox / Changes: created is informational; due/disappear need a decision."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus.academic_state import persist_obligations
from nexus.changes import list_inbox_changes
from nexus.models import Task


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


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _seed_inbox(tmp_path: Path) -> None:
    friday = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    monday = friday + timedelta(days=3)
    keep = _task(id_="bspace_ical:keep", title="Assignment 4", due_at=friday)
    gone = _task(id_="bspace_ical:gone", title="Lab 2", due_at=friday, tags=["lab"])
    persist_obligations(tmp_path, [keep, gone])
    persist_obligations(
        tmp_path,
        [_task(id_="bspace_ical:keep", title="Assignment 4", due_at=monday)],
        reconcile_missing=True,
    )


def test_created_is_informational(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    persist_obligations(
        tmp_path,
        [_task(id_="bspace_ical:a", title="Homework 1", due_at=due)],
    )
    items = list_inbox_changes(tmp_path)
    assert items
    assert all(item.kind == "created" for item in items)
    assert all(item.needs_decision is False for item in items)


def test_due_changed_and_disappeared_need_decision(tmp_path: Path) -> None:
    _seed_inbox(tmp_path)
    items = list_inbox_changes(tmp_path, limit=10)
    by_kind = {item.kind: item for item in items}
    assert by_kind["created"].needs_decision is False
    assert by_kind["due_changed"].needs_decision is True
    assert by_kind["disappeared"].needs_decision is True
    assert by_kind["due_changed"].obligation_id
    assert by_kind["disappeared"].obligation_id


def test_decisions_only_filters(tmp_path: Path) -> None:
    _seed_inbox(tmp_path)
    items = list_inbox_changes(tmp_path, limit=10, decisions_only=True)
    assert items
    assert all(item.needs_decision for item in items)
    assert {item.kind for item in items} == {"due_changed", "disappeared"}


def test_work_block_never_appears(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)
    persist_obligations(
        tmp_path,
        [
            _task(
                id_="manual:wb",
                title="工作块：Lab",
                due_at=due,
                source="manual",
                tags=["work_block"],
            ),
            _task(id_="bspace_ical:a", title="Homework 1", due_at=due),
        ],
    )
    items = list_inbox_changes(tmp_path)
    assert items
    assert all(item.kind != "work_block" for item in items)
    assert all("工作块" not in item.title for item in items)
    assert all("工作块" not in item.text for item in items)


def test_texts_are_nonempty_chinese(tmp_path: Path) -> None:
    _seed_inbox(tmp_path)
    items = list_inbox_changes(tmp_path, limit=10)
    assert items
    for item in items:
        assert item.text.strip()
        assert _has_cjk(item.text)


def test_change_item_to_dict(tmp_path: Path) -> None:
    due = datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc)
    persist_obligations(
        tmp_path,
        [_task(id_="bspace_ical:a", title="Homework 1", due_at=due)],
    )
    payload = list_inbox_changes(tmp_path)[0].to_dict()
    assert payload["kind"] == "created"
    assert payload["needs_decision"] is False
    assert payload["title"]
    assert payload["text"]
    assert payload["obligation_id"]
