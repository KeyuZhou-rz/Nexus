from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nexus.course_state import UNASSIGNED_COURSE, build_course_states
from nexus.core.workload import EFFORT_HOURS_BY_TAG
from nexus.core.models import Task


def _task(
    id_: str,
    title: str,
    due_at: datetime | None,
    *,
    course: str | None = "CS",
    tags: list[str] | None = None,
    source: str = "brightspace",
    status: str = "open",
) -> Task:
    return Task(
        id=id_,
        title=title,
        due_at=due_at,
        source=source,
        course=course,
        status=status,
        tags=tags or ["assignment"],
    )


def test_empty_tasks_yield_no_courses() -> None:
    assert build_course_states([]) == []


def test_one_course_two_tasks() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    soon = _task("a", "Homework 1", now + timedelta(days=1), course="CS")
    later = _task("b", "Homework 2", now + timedelta(days=4), course="CS")
    states = build_course_states([soon, later], now=now)
    assert len(states) == 1
    state = states[0]
    assert state.course == "CS"
    assert state.open_count == 2
    assert state.remaining_hours == EFFORT_HOURS_BY_TAG["assignment"] * 2
    assert state.next_due_at == soon.due_at
    assert state.next_title == "Homework 1"
    assert state.risk_band == "unscored"
    assert state.overdue_count == 0
    assert state.pace is None
    payload = state.to_dict()
    assert payload["next_due_at"] == soon.due_at.isoformat()
    assert payload["risk_band"] == "unscored"


def test_overdue_vs_future() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    overdue = _task("late", "Lab 1", now - timedelta(hours=3), course="EE", tags=["lab"])
    future = _task("soon", "Homework 3", now + timedelta(days=2), course="CS")
    states = {row.course: row for row in build_course_states([overdue, future], now=now)}
    assert states["EE"].risk_band == "overdue"
    assert states["EE"].overdue_count == 1
    assert states["CS"].risk_band == "unscored"
    assert states["CS"].overdue_count == 0
    assert all(row.risk_band in {"overdue", "unscored"} for row in states.values())


def test_work_block_ignored() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    homework = _task("hw", "Homework 1", now + timedelta(days=1), course="CS")
    block = _task(
        "wb",
        "工作块：Lab",
        now + timedelta(hours=2),
        course="CS",
        tags=["work_block", "manual"],
        source="manual",
    )
    states = build_course_states([homework, block], now=now)
    assert len(states) == 1
    assert states[0].open_count == 1
    assert states[0].next_title == "Homework 1"
    assert states[0].remaining_hours == EFFORT_HOURS_BY_TAG["assignment"]


def test_holiday_and_meeting_ignored() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    homework = _task("hw", "Circuits Homework 2", now + timedelta(days=2), course="EE")
    holiday = _task(
        "h",
        "Chinese Valentine's Day",
        now + timedelta(days=1),
        course="Holidays in China",
        tags=["calendar"],
        source="gcal",
    )
    meeting = _task(
        "oh",
        "Office Hours",
        now + timedelta(hours=3),
        course="EE",
        tags=["meeting"],
        source="gcal",
    )
    states = build_course_states([homework, holiday, meeting], now=now)
    assert [row.course for row in states] == ["EE"]
    assert states[0].open_count == 1
    assert states[0].next_title == "Circuits Homework 2"


def test_remaining_hours_from_completions() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    started = _task("hw", "Homework 1", now + timedelta(days=2), course="CS")
    completions = {
        "hw": {"started_at": (now - timedelta(hours=1)).isoformat()},
    }
    states = build_course_states([started], completions=completions, now=now)
    assert states[0].remaining_hours == EFFORT_HOURS_BY_TAG["assignment"] - 1.0

    done_logged = {
        "hw": {"done_at": now.isoformat(), "effort_minutes": 90},
    }
    spent_done = build_course_states([started], completions=done_logged, now=now)
    assert spent_done[0].remaining_hours == EFFORT_HOURS_BY_TAG["assignment"] - 1.5


def test_sort_order_by_next_due_then_remaining_hours() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    later_heavy = _task(
        "p",
        "Graphics project",
        now + timedelta(days=3),
        course="Graphics",
        tags=["project"],
    )
    soon_quiz = _task("q", "EE quiz", now + timedelta(days=1), course="EE", tags=["quiz"])
    soon_hw = _task("h", "CS homework", now + timedelta(days=1), course="CS")
    states = build_course_states([later_heavy, soon_hw, soon_quiz], now=now)
    assert [row.course for row in states] == ["EE", "CS", "Graphics"]
    assert states[0].remaining_hours == EFFORT_HOURS_BY_TAG["quiz"]
    assert states[1].remaining_hours == EFFORT_HOURS_BY_TAG["assignment"]
    assert states[2].remaining_hours == EFFORT_HOURS_BY_TAG["project"]


def test_empty_course_uses_unassigned_label() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    untitled = _task("m", "Manual homework", now + timedelta(days=1), course=None, source="manual")
    blank = _task("n", "Other homework", now + timedelta(days=2), course="  ", source="manual")
    states = build_course_states([untitled, blank], now=now)
    assert len(states) == 1
    assert states[0].course == UNASSIGNED_COURSE
    assert states[0].open_count == 2


def test_done_tasks_and_only_noise_omitted() -> None:
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    done = _task("d", "Old homework", now + timedelta(days=1), course="CS", status="done")
    holiday = _task(
        "h",
        "Christmas",
        now + timedelta(days=1),
        course="Holidays",
        tags=["calendar"],
        source="gcal",
    )
    assert build_course_states([done, holiday], now=now) == []


def test_pace_from_course_history() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    project = _task(
        "p",
        "Graphics project",
        now + timedelta(days=3),
        course="Graphics",
        tags=["project"],
    )
    completions = {
        "old-a": {
            "done_at": (now - timedelta(days=6, hours=3)).isoformat(),
            "due_at": (now - timedelta(days=6)).isoformat(),
            "course": "Graphics",
            "effort_key": "assignment",
            "effort_minutes": 120,
        },
    }
    states = build_course_states([project], completions=completions, now=now)
    assert states[0].pace and "提前 3 小时" in states[0].pace
