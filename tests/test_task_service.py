from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nexus.aggregation import run_aggregation
from nexus.config import AppConfig
from nexus.models import Task
from nexus.storage import load_tasks, save_tasks
from nexus.task_service import (
    add_manual_task,
    apply_completions,
    load_completions,
    mark_task_done,
)


def test_add_manual_task_persists_normalized_user_owned_item(tmp_path: Path) -> None:
    due = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)

    task = add_manual_task(
        tmp_path,
        title="  Review deadlock  ",
        due_at=due,
        course=" OS ",
        priority=2,
        tags=[" exam ", ""],
        notes=" chapter 7 ",
    )

    saved = load_tasks(tmp_path / "tasks.json")
    assert saved == [task]
    assert task.source == "manual"
    assert task.title == "Review deadlock"
    assert task.course == "OS"
    assert task.tags == ["manual", "exam"]
    assert task.snippet == "chapter 7"


def test_successful_aggregation_preserves_manual_tasks(tmp_path: Path) -> None:
    manual = add_manual_task(tmp_path, title="Read chapter 7")
    config = AppConfig(data_dir=tmp_path, feeds_path=tmp_path / "feeds.json")

    result = run_aggregation(
        include_google=False,
        include_canvas=False,
        data_dir=tmp_path,
        config=config,
    )

    assert [task.id for task in result.tasks] == [manual.id]
    assert [task.id for task in load_tasks(tmp_path / "tasks.json")] == [manual.id]


def test_add_manual_task_rejects_empty_title(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="title"):
        add_manual_task(tmp_path, title="  ")


def test_mark_task_done_records_completion_and_reflects_status(tmp_path: Path) -> None:
    due = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    save_tasks([Task(id="gcal:abc", title="CS homework", due_at=due, source="gcal")], tmp_path / "tasks.json")

    marked = mark_task_done(tmp_path, "gcal:abc")

    assert marked is not None
    assert marked.status == "done"
    assert marked.done_at is not None
    saved = load_tasks(tmp_path / "tasks.json")
    assert saved[0].status == "done"
    assert saved[0].done_at is not None
    assert "gcal:abc" in load_completions(tmp_path)


def test_apply_completions_restores_done_on_fresh_source_task(tmp_path: Path) -> None:
    due = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    save_tasks([Task(id="gcal:abc", title="CS homework", due_at=due, source="gcal")], tmp_path / "tasks.json")
    mark_task_done(tmp_path, "gcal:abc")

    # Simulate a re-aggregation that re-fetches the task as a fresh "open" object.
    fresh = [Task(id="gcal:abc", title="CS homework", due_at=due, source="gcal")]
    applied = apply_completions(fresh, load_completions(tmp_path))

    assert applied[0].status == "done"
    assert applied[0].done_at is not None


def test_apply_completions_ignores_malformed_entry() -> None:
    due = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    tasks = [Task(id="gcal:abc", title="X", due_at=due, source="gcal")]
    assert apply_completions(tasks, {"gcal:abc": "not-a-dict"})[0].status == "open"
