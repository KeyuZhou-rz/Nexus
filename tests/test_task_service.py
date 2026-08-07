from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nexus.aggregation import run_aggregation
from nexus.config import AppConfig
from nexus.storage import load_tasks
from nexus.task_service import add_manual_task


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
