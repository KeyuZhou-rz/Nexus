from __future__ import annotations

from datetime import datetime, timedelta

from nexus.core.workload import (
    build_judgment,
    effort_key,
    estimate_effort_hours,
    is_actionable_deadline,
    resolve_effort,
)
from nexus.core.models import Task


def _task(id_: str, title: str, due_at: datetime, tags: list[str] | None = None) -> Task:
    return Task(id=id_, title=title, due_at=due_at, source="gcal", tags=tags or [])


def test_work_block_is_not_actionable() -> None:
    block = _task("wb", "工作块：Lab", datetime.now(), tags=["work_block", "manual"])
    block.source = "manual"
    assert is_actionable_deadline(block) is False


def test_meeting_is_not_actionable() -> None:
    now = datetime.now()
    office = _task("oh", "Office Hours", now, tags=["meeting"])
    office.source = "manual"
    assert is_actionable_deadline(office) is False


def test_estimate_effort_hours_uses_completion_history() -> None:
    task = _task("hw", "Circuits Homework 2", datetime.now(), tags=["assignment"])
    assert estimate_effort_hours(task) == 3.0
    hours, basis = resolve_effort(
        task,
        {"old": {"effort_key": "assignment", "effort_minutes": 90}},
    )
    assert basis == "history"
    assert hours == 2.5  # (1*1.5 + 2*3) / 3
    assert effort_key(task) == "assignment"


def test_resolve_effort_uses_median_after_three_samples() -> None:
    task = _task("hw", "Circuits Homework 2", datetime.now(), tags=["assignment"])
    completions = {
        "a": {"effort_key": "assignment", "effort_minutes": 90},
        "b": {"effort_key": "assignment", "effort_minutes": 90},
        "c": {"effort_key": "assignment", "effort_minutes": 90},
    }
    hours, basis = resolve_effort(task, completions)
    assert basis == "history"
    assert hours == 1.5


def test_build_judgment_includes_course_pace() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    project = _task("p", "Graphics project", now + timedelta(days=3), tags=["project"])
    project.course = "Graphics"
    homework = _task("h", "CS homework", now + timedelta(days=1), tags=["assignment"])
    homework.course = "CS"
    completions = {
        "old-a": {
            "done_at": (now - timedelta(days=6, hours=3)).isoformat(),
            "due_at": (now - timedelta(days=6)).isoformat(),
            "course": "Graphics",
            "effort_key": "assignment",
            "effort_minutes": 120,
        },
        "old-p": {
            "done_at": (now - timedelta(days=8)).isoformat(),
            "effort_key": "project",
            "effort_minutes": 450,
            "course": "Graphics",
        },
    }
    j = build_judgment([homework, project], now=now, completions=completions)
    assert "明天" in (j.deadline or "")
    assert j.pace and "提前 3 小时" in j.pace
    assert "Graphics" in (j.forecast or "")
    assert "预计需要" in (j.forecast or "")
    assert j.anchor_task_id == "p"


def test_build_judgment_forecast_uses_history() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    task = _task("hw", "Circuits Homework 2", now + timedelta(days=2), tags=["assignment"])
    task.course = "EE"
    j = build_judgment(
        [task],
        now=now,
        completions={"old": {"effort_key": "assignment", "effort_minutes": 90}},
    )
    assert j.effort_basis == "history"
    assert "过去工作速度" in (j.forecast or "")
    assert "2.5h" in (j.forecast or "")
    assert "截止" in (j.deadline or "")
    assert "第一部分" in (j.suggestion or "")


def test_estimate_effort_hours_by_tag() -> None:
    assert estimate_effort_hours(_task("1", "Project", datetime.now(), tags=["project"])) == 8.0
    assert estimate_effort_hours(_task("2", "Catch up on notes", datetime.now())) == 2.0
    # word-boundary matching: "example" must not match the "exam" tag
    assert estimate_effort_hours(_task("3", "Example problems", datetime.now())) == 2.0


def test_estimate_effort_hours_specific_tag_wins() -> None:
    assert estimate_effort_hours(_task("5", "Final exam", datetime.now())) == 12.0


def test_build_judgment_overdue() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    tasks = [_task("1", "CS 作业", now - timedelta(hours=2))]
    j = build_judgment(tasks, done_ids=set(), now=now)
    assert j.deadline is not None
    assert "已截止" in j.deadline
    assert j.anchor_task_id == "1"
    assert "今晚" in (j.suggestion or "")
    assert j.available is True
    payload = j.to_dict()
    assert payload["available"] is True
    assert payload["task_id"] == "1"
    assert payload["suggestion"]


def test_build_judgment_skips_done() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    tasks = [_task("1", "CS 作业", now + timedelta(days=1))]
    assert build_judgment(tasks, done_ids={"1"}, now=now).deadline is None


def test_build_judgment_skips_done_status() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    task = _task("1", "CS 作业", now + timedelta(days=1))
    task.status = "done"
    assert build_judgment([task], now=now).available is False


def test_build_judgment_no_tasks() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    empty = build_judgment([], now=now)
    assert empty.deadline is None
    assert empty.available is False
    assert empty.to_dict()["available"] is False


def test_holiday_calendar_is_not_actionable() -> None:
    holiday = _task("h", "Chinese Valentine's Day", datetime.now(), tags=["calendar"])
    holiday.course = "Holidays in China"
    homework = _task("hw", "Circuits Homework 2", datetime.now(), tags=["assignment"])
    assert is_actionable_deadline(holiday) is False
    assert is_actionable_deadline(homework) is True
    assert is_actionable_deadline(_task("ex", "Example problems", datetime.now(), tags=["calendar"])) is False


def test_build_judgment_skips_holiday_for_real_deadline() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    holiday = _task("h", "Chinese Valentine's Day", now + timedelta(days=1), tags=["calendar"])
    holiday.course = "Holidays in China"
    homework = _task("hw", "Circuits Homework 2", now + timedelta(days=3), tags=["assignment"])
    j = build_judgment([holiday, homework], now=now)
    assert j.anchor_task_id == "hw"
    assert "Homework" in (j.title or "")


def test_build_judgment_empty_when_only_calendar_noise() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    holiday = _task("h", "Chinese Valentine's Day", now + timedelta(days=1), tags=["calendar"])
    holiday.course = "Holidays in China"
    assert build_judgment([holiday], now=now).available is False


def test_build_judgment_week_items_cover_each_actionable() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    homework = _task("h", "CS homework", now + timedelta(days=1), tags=["assignment"])
    homework.course = "CS"
    project = _task("p", "Graphics project", now + timedelta(days=3), tags=["project"])
    project.course = "Graphics"
    later = _task("x", "Far exam", now + timedelta(days=12), tags=["exam"])
    later.course = "CS"
    j = build_judgment([homework, project, later], now=now)
    ids = [item.task_id for item in j.week_items]
    assert ids[0] in {"h", "p"}
    assert set(ids) == {"h", "p"}
    assert sum(1 for item in j.week_items if item.recommended) == 1
    assert all(item.label for item in j.week_items)
    payload = j.to_dict()
    assert [row["task_id"] for row in payload["week_items"]] == ids


def test_build_judgment_overloaded_and_learning_why() -> None:
    now = datetime(2026, 8, 17, 20, 0).astimezone()
    task = _task("lab", "Circuits Lab 5", now + timedelta(hours=2), tags=["lab"])
    task.course = "EE"
    j = build_judgment(
        [task],
        now=now,
        learning_focus=[{"course_id": "EE", "name": "Op-amp"}],
    )
    assert j.overloaded is True
    assert "今晚至少完成" in (j.suggestion or "")
