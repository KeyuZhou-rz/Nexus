from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nexus.core import aggregation
from nexus.core.config import AppConfig
from nexus.core.models import FeedSource, Task
from nexus.core.storage import load_tasks, save_feeds, save_tasks


class _GoogleFailStub:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_tasks(self):
        raise RuntimeError("missing google deps")


class _BrightspaceFailStub:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_tasks(self):
        raise RuntimeError("brightspace timeout")


class _BrightspaceSuccessStub:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_tasks(self):
        return [_task("new-1", "New assignment")]


def _task(task_id: str, title: str, source: str = "brightspace") -> Task:
    return Task(
        id=task_id,
        title=title,
        due_at=datetime(2026, 3, 1),
        source=source,
        tags=["assignment"],
    )


def test_preserve_existing_tasks_when_sources_fail(monkeypatch):
    existing = [_task("old-1", "Existing Task")]
    saved = {}

    monkeypatch.setattr(aggregation, "load_tasks", lambda path=None: list(existing))
    monkeypatch.setattr(
        aggregation,
        "load_feeds",
        lambda path=None: [
            type("F", (), {
                "kind": "brightspace_ical",
                "name": "Course",
                "url": "https://example.com/feed.ics",
                "enabled": True,
                "course": "C1",
                "audience": "ui",
                "mode": "default",
            })()
        ],
    )
    monkeypatch.setattr(aggregation, "BrightspaceAggregator", _BrightspaceFailStub)
    monkeypatch.setattr(aggregation, "GoogleAggregator", _GoogleFailStub)
    monkeypatch.setattr(aggregation, "_google_configured", lambda config: True)
    monkeypatch.setattr(aggregation, "save_tasks", lambda tasks, path=None: saved.setdefault("tasks", list(tasks)))

    result = aggregation.run_aggregation(include_google=True, include_canvas=False)

    # Per-source snapshot: old tasks preserved individually from each source
    assert len(result.tasks) == 1  # old Brightspace task preserved
    assert result.tasks[0].id == "old-1"
    assert any("preserving 1 old tasks" in w for w in result.warnings)


def test_no_feed_config_reports_warning(monkeypatch):
    saved = {}
    monkeypatch.setattr(aggregation, "load_tasks", lambda path=None: [])
    monkeypatch.setattr(aggregation, "load_feeds", lambda path=None: [])
    monkeypatch.setattr(aggregation, "save_tasks", lambda tasks, path=None: saved.setdefault("tasks", list(tasks)))

    result = aggregation.run_aggregation(include_google=False)

    assert result.tasks == []
    assert result.errors == []
    assert any("Brightspace feeds not configured" in warning for warning in result.warnings)
    assert result.source_status == {"brightspace": "skipped", "google": "disabled", "canvas": "skipped"}
    assert saved["tasks"] == []


def test_partial_configuration_succeeds_with_warning(monkeypatch):
    monkeypatch.setattr(aggregation, "load_tasks", lambda path=None: [])
    monkeypatch.setattr(
        aggregation,
        "load_feeds",
        lambda path=None: [
            type("F", (), {
                "kind": "brightspace_ical", "name": "Course",
                "url": "file:///feed.ics", "enabled": True,
                "course": "C1", "audience": "ui", "mode": "default",
            })()
        ],
    )
    monkeypatch.setattr(aggregation, "BrightspaceAggregator", _BrightspaceSuccessStub)
    monkeypatch.setattr(aggregation, "_google_configured", lambda config: False)
    monkeypatch.setattr(aggregation, "save_tasks", lambda tasks, path=None: None)

    result = aggregation.run_aggregation(include_google=True)
    assert result.errors == []
    assert len(result.tasks) == 1
    assert result.source_status == {"brightspace": "succeeded", "google": "skipped", "canvas": "skipped"}
    assert any("Google credentials" in warning for warning in result.warnings)


def test_disabled_and_skipped_sources_preserve_mixed_snapshot(tmp_path: Path):
    existing = [
        _task("archive", "Archived assignment", "brightspace"),
        _task("calendar", "Calendar event", "gcal"),
        _task("mail", "Unread mail", "gmail"),
        _task("legacy-google", "Legacy Google item", "google"),
        _task("canvas", "Canvas assignment", "canvas"),
        _task("manual", "User task", "manual"),
        _task("extension", "Extension task", "custom_provider"),
    ]
    save_tasks(existing, tmp_path / "tasks.json")
    config = AppConfig(data_dir=tmp_path)

    result = aggregation.run_aggregation(
        include_google=False,
        include_canvas=False,
        data_dir=tmp_path,
        config=config,
    )

    expected_ids = {task.id for task in existing}
    assert {task.id for task in result.tasks} == expected_ids
    assert {task.id for task in load_tasks(tmp_path / "tasks.json")} == expected_ids
    assert result.source_status == {
        "brightspace": "skipped",
        "google": "disabled",
        "canvas": "disabled",
    }


def test_successful_feed_refresh_preserves_archive_only_brightspace_tasks(
    monkeypatch,
    tmp_path: Path,
):
    archive_task = _task(
        "bspace_pw:course-1:assignment-1",
        "Assignment discovered by archive sync",
    )
    stale_feed_task = _task("ical:stale", "Old feed assignment")
    save_tasks([archive_task, stale_feed_task], tmp_path / "tasks.json")
    save_feeds(
        [
            FeedSource(
                kind="brightspace_ical",
                name="Course feed",
                url="file:///unused.ics",
            )
        ],
        tmp_path / "feeds.json",
    )
    monkeypatch.setattr(aggregation, "BrightspaceAggregator", _BrightspaceSuccessStub)

    result = aggregation.run_aggregation(
        include_google=False,
        include_canvas=False,
        data_dir=tmp_path,
        config=AppConfig(data_dir=tmp_path),
    )

    assert {task.id for task in result.tasks} == {archive_task.id, "new-1"}
    assert {task.id for task in load_tasks(tmp_path / "tasks.json")} == {
        archive_task.id,
        "new-1",
    }
    assert result.source_status["brightspace"] == "succeeded"
