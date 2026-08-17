"""Inbox / Changes: created is informational; due/disappear need a decision."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nexus.academic_state import persist_obligations
from nexus.changes import acknowledge_change, change_key, list_inbox_changes, mail_change_items
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


def test_mail_signals_skip_newsletters_and_keep_due_notices() -> None:
    due = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    mail = [
        Task(id="gmail:1", title="NVIDIA Developer News", due_at=None, source="gmail", tags=["email", "unread", "general"]),
        Task(id="gmail:2", title="Keyu, we are hiring", due_at=None, source="gmail", tags=["email", "unread", "general"]),
        Task(
            id="gmail:3",
            title="Your IT equipment is about to become due",
            due_at=None,
            source="gmail",
            tags=["email", "unread", "general"],
        ),
        Task(
            id="gmail:4",
            title="Your Student Visa Information has been received",
            due_at=None,
            source="gmail",
            tags=["email", "unread", "general"],
        ),
        Task(
            id="gmail:5",
            title="Homework 2 posted",
            due_at=due,
            source="gmail",
            tags=["email", "unread", "course_notification"],
        ),
    ]
    items = mail_change_items(mail)
    titles = [item.title for item in items]
    assert "NVIDIA Developer News" not in titles
    assert "Keyu, we are hiring" not in titles
    assert any("equipment" in item.title for item in items)
    assert any(item.kind == "mail" and item.needs_decision for item in items)
    visa = next(item for item in items if "Visa" in item.title)
    assert visa.needs_decision is False
    homework = next(item for item in items if "Homework" in item.title)
    assert homework.needs_decision is True
    assert all(item.text.startswith("邮件：") for item in items)


def test_list_inbox_changes_can_include_mail(tmp_path: Path) -> None:
    persist_obligations(
        tmp_path,
        [_task(id_="bspace_ical:a", title="Homework 1", due_at=datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc))],
    )
    mail = [
        Task(
            id="gmail:eq",
            title="Your IT equipment is about to become due",
            due_at=None,
            source="gmail",
            tags=["email", "general"],
        )
    ]
    items = list_inbox_changes(tmp_path, limit=8, tasks=mail)
    kinds = {item.kind for item in items}
    assert "created" in kinds
    assert "mail" in kinds


def test_acknowledge_hides_decision(tmp_path: Path) -> None:
    _seed_inbox(tmp_path)
    items = list_inbox_changes(tmp_path, limit=10, decisions_only=True)
    assert items
    first = items[0]
    acknowledge_change(tmp_path, change_key(first))
    later = list_inbox_changes(tmp_path, limit=10, decisions_only=True)
    assert change_key(first) not in {change_key(item) for item in later}
