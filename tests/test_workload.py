from __future__ import annotations

from datetime import datetime, timedelta

from nexus.intelligence.workload import build_judgment, estimate_effort_hours
from nexus.models import Task


def _task(id_: str, title: str, due_at: datetime, tags: list[str] | None = None) -> Task:
    return Task(id=id_, title=title, due_at=due_at, source="gcal", tags=tags or [])


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
    assert "已过期" in (j.forecast or "")
    assert j.anchor_task_id == "1"
    assert "第一步" in (j.suggestion or "")


def test_build_judgment_skips_done() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    tasks = [_task("1", "CS 作业", now + timedelta(days=1))]
    assert build_judgment(tasks, done_ids={"1"}, now=now).deadline is None


def test_build_judgment_no_tasks() -> None:
    now = datetime(2026, 8, 17, 9, 0).astimezone()
    assert build_judgment([], now=now).deadline is None
