from __future__ import annotations

from datetime import datetime

from nexus import aggregation
from nexus.models import Task


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

    result = aggregation.run_aggregation(include_google=True)

    assert result.tasks == existing
    assert any("preserving existing tasks.json" in err for err in result.errors)
    assert saved["tasks"] == existing


def test_no_feed_config_reports_warning(monkeypatch):
    saved = {}
    monkeypatch.setattr(aggregation, "load_tasks", lambda path=None: [])
    monkeypatch.setattr(aggregation, "load_feeds", lambda path=None: [])
    monkeypatch.setattr(aggregation, "save_tasks", lambda tasks, path=None: saved.setdefault("tasks", list(tasks)))

    result = aggregation.run_aggregation(include_google=False)

    assert result.tasks == []
    assert result.errors == []
    assert any("Brightspace feeds not configured" in warning for warning in result.warnings)
    assert result.source_status == {"brightspace": "skipped", "google": "disabled"}
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
    assert result.source_status == {"brightspace": "succeeded", "google": "skipped"}
    assert any("Google credentials" in warning for warning in result.warnings)
