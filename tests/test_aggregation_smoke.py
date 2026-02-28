from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from nexus import aggregation
from nexus.models import Task


@dataclass
class _Feed:
    kind: str
    name: str
    url: str
    enabled: bool = True
    course: str | None = None
    audience: str = "ui"
    mode: str = "default"


class _BrightspaceStub:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_tasks(self):
        return [
            Task(
                id="b1",
                title="Assignment A",
                due_at=datetime(2026, 3, 1),
                source="brightspace",
                tags=["assignment"],
            )
        ]


class _GoogleStub:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_tasks(self):
        return [
            Task(
                id="g1",
                title="Calendar Event",
                due_at=datetime(2026, 3, 2),
                source="gcal",
                tags=["calendar"],
            )
        ]


def test_run_aggregation_smoke(monkeypatch):
    captured = {}

    def _save(tasks):
        captured["count"] = len(tasks)

    monkeypatch.setattr(aggregation, "load_feeds", lambda: [_Feed("brightspace_ical", "c1", "file://x")])
    monkeypatch.setattr(aggregation, "save_tasks", _save)
    monkeypatch.setattr(aggregation, "BrightspaceAggregator", _BrightspaceStub)
    monkeypatch.setattr(aggregation, "GoogleAggregator", _GoogleStub)

    result = aggregation.run_aggregation(include_google=True)
    assert not result.errors
    assert len(result.tasks) == 2
    assert captured["count"] == 2
