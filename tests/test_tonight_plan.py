from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from nexus.core.models import Task
from nexus.tonight_plan import (
    TonightPlan,
    build_tonight_plan,
    load_plan_decision,
    next_plan_focus_id,
    save_plan_decision,
)


def _task(
    id_: str,
    title: str,
    due_at: datetime,
    *,
    tags: list[str] | None = None,
    course: str | None = None,
    source: str = "gcal",
    status: str = "open",
) -> Task:
    return Task(
        id=id_,
        title=title,
        due_at=due_at,
        source=source,
        course=course,
        status=status,  # type: ignore[arg-type]
        tags=tags or [],
    )


def test_no_tasks() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    plan = build_tonight_plan([], now=now)
    assert plan.available is False
    assert plan.text is None
    assert plan.task_id is None
    assert plan.window_start is None


def test_focus_honored() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    overdue = _task("old", "EE homework", now - timedelta(hours=3), tags=["assignment"], course="EE")
    later = _task("focus", "CS homework", now + timedelta(days=2), tags=["assignment"], course="CS")
    plan = build_tonight_plan([overdue, later], now=now, focus_id="focus")
    assert plan.available is True
    assert plan.task_id == "focus"
    assert plan.course == "CS"
    assert "CS" in (plan.text or "")


def test_overdue_preferred_via_judgment() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    overdue = _task("old", "EE homework", now - timedelta(hours=3), tags=["assignment"], course="EE")
    later = _task("later", "CS homework", now + timedelta(days=2), tags=["assignment"], course="CS")
    plan = build_tonight_plan([later, overdue], now=now)
    assert plan.available is True
    assert plan.task_id == "old"
    assert plan.reason and "截止" in plan.reason


def test_window_clipped_at_22() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    homework = _task("hw", "CS homework", now + timedelta(days=1), tags=["assignment"], course="CS")
    plan = build_tonight_plan([homework], now=now)
    assert plan.available is True
    assert plan.hours == 3.0
    assert plan.window_start == now
    assert plan.window_end == now.replace(hour=22, minute=0, second=0, microsecond=0)
    assert "20:00" in (plan.text or "")
    assert "22:00" in (plan.text or "")
    assert "第一部分" in (plan.text or "")


def test_after_22_still_available_with_fallback_window() -> None:
    now = datetime(2026, 8, 17, 22, 30).astimezone()
    homework = _task("hw", "CS homework", now + timedelta(days=1), tags=["assignment"], course="CS")
    plan = build_tonight_plan([homework], now=now)
    assert plan.available is True
    assert plan.task_id == "hw"
    assert plan.text and "今晚剩下的时间先做第一部分" in plan.text
    assert plan.window_start is not None and plan.window_end is not None
    span = (plan.window_end - plan.window_start).total_seconds() / 3600.0
    assert 0 < span <= 1.0
    assert plan.hours == 3.0


def test_work_block_ignored() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    block = _task(
        "wb",
        "工作块：Lab",
        now + timedelta(hours=2),
        tags=["work_block", "manual"],
        source="manual",
    )
    meeting = _task("mt", "Office Hours", now + timedelta(hours=1), tags=["meeting"], source="manual")
    holiday = _task("h", "Chinese Valentine's Day", now + timedelta(days=1), tags=["calendar"])
    holiday.course = "Holidays in China"
    homework = _task("hw", "CS homework", now + timedelta(days=1), tags=["assignment"], course="CS")

    mixed = build_tonight_plan([block, meeting, holiday, homework], now=now)
    assert mixed.task_id == "hw"

    only_noise = build_tonight_plan([block, meeting, holiday], now=now)
    assert only_noise.available is False
    assert only_noise.text is None

    forced_block = build_tonight_plan([block, homework], now=now, focus_id="wb")
    assert forced_block.task_id == "hw"


def test_to_dict_keys() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    homework = _task("hw", "CS homework", now + timedelta(days=1), tags=["assignment"], course="CS")
    payload = build_tonight_plan([homework], now=now).to_dict()
    assert set(payload) == {
        "available",
        "task_id",
        "title",
        "course",
        "hours",
        "window_start",
        "window_end",
        "text",
        "reason",
    }
    assert payload["available"] is True
    assert payload["task_id"] == "hw"
    assert payload["window_start"] == now.isoformat()
    empty = build_tonight_plan([], now=now).to_dict()
    assert empty["available"] is False
    assert empty["text"] is None
    assert empty["task_id"] is None
    assert isinstance(TonightPlan(**{**empty, "window_start": None, "window_end": None}), TonightPlan)


def test_accept_persists_for_today_only(tmp_path: Path) -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    homework = _task("hw", "CS homework", now + timedelta(days=1), tags=["assignment"], course="CS")
    plan = build_tonight_plan([homework], now=now)
    saved = save_plan_decision(tmp_path, plan, status="accepted", now=now)
    assert saved["status"] == "accepted"
    assert saved["for_date"] == "2026-08-17"
    assert load_plan_decision(tmp_path, now=now)["task_id"] == "hw"
    tomorrow = now + timedelta(days=1)
    assert load_plan_decision(tmp_path, now=tomorrow) is None
    assert not (tmp_path / "google_token.json").exists()


def test_adjust_cycles_to_the_other_open_obligation() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    first = _task("a", "EE homework", now + timedelta(days=1), tags=["assignment"], course="EE")
    second = _task("b", "CS homework", now + timedelta(days=2), tags=["assignment"], course="CS")
    assert next_plan_focus_id([first, second], "a") == "b"
    assert next_plan_focus_id([first, second], "b") == "a"
    assert next_plan_focus_id([first], "a") is None


def test_adjust_skips_completed_task() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    first = _task("a", "EE homework", now + timedelta(days=1), tags=["assignment"], course="EE")
    second = _task("b", "CS homework", now + timedelta(days=2), tags=["assignment"], course="CS")
    done = {"a": {"done_at": now.isoformat()}}
    assert next_plan_focus_id([first, second], "b", completions=done) is None
