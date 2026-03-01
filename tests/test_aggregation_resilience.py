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

    monkeypatch.setattr(aggregation, "load_tasks", lambda: list(existing))
    monkeypatch.setattr(
        aggregation,
        "load_feeds",
        lambda: [
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
    monkeypatch.setattr(aggregation, "save_tasks", lambda tasks: saved.setdefault("tasks", list(tasks)))

    result = aggregation.run_aggregation(include_google=True)

    assert result.tasks == existing
    assert any("preserving existing tasks.json" in err for err in result.errors)
    assert saved["tasks"] == existing


def test_no_feed_config_reports_error(monkeypatch):
    saved = {}
    monkeypatch.setattr(aggregation, "load_tasks", lambda: [])
    monkeypatch.setattr(aggregation, "load_feeds", lambda: [])
    monkeypatch.setattr(aggregation, "save_tasks", lambda tasks: saved.setdefault("tasks", list(tasks)))

    result = aggregation.run_aggregation(include_google=False)

    assert result.tasks == []
    assert any("Brightspace feeds not configured" in err for err in result.errors)
    assert saved["tasks"] == []
