from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.intelligence import briefing as briefing_module
from nexus.intelligence.briefing import Briefing, BriefingItem, _inject_learning_context


class _SnapshotStub:
    """Mimics MasterySnapshot interface for decaying concept injection."""

    def __init__(self, name: str, mastery: float, urgency: str):
        self.concept_name = name
        self.current_mastery = mastery
        self.urgency = urgency
        self.days_since_review = 5.0
        self.decay_rate = 0.3
        self.review_count = 2
        self.streak = 1


class _MemoryAPIStub:
    """Mimics MemoryAPI interface for briefing context injection."""

    def get_memory_context(self):
        ctx = type("Ctx", (), {})()
        ctx.weak_points = ["op-amp feedback", "kirchhoff"]
        ctx.review_queue = ["bode plot"]
        ctx.low_mastery_topics = ["transient response"]
        ctx.context_topics = ["bode plot", "transient response"]
        return ctx

    def get_decaying_concepts(self, threshold: float = 0.4):
        return [
            _SnapshotStub("bode plot", 0.15, "critical"),
            _SnapshotStub("transient response", 0.35, "decaying"),
        ]


class _SummaryStub:
    class _Item:
        def __init__(self, text: str, file_name: str):
            self.text = text
            self.metadata = {"file_name": file_name}
            self.distance = 0.1

    items = [_Item("Negative feedback stabilizes gain.", "lecture_05.md")]


def test_inject_learning_context_appends_items(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing_module, "MemoryAPI", lambda store: _MemoryAPIStub())
    monkeypatch.setattr(briefing_module, "query_knowledge", lambda *args, **kwargs: _SummaryStub())

    config = type("C", (), {"data_dir": tmp_path})()

    base = Briefing(
        todo=[],
        schedule=[],
        warnings=[],
        task_index={"t1": type("T", (), {"course": "EE201"})()},
        focus=[
            BriefingItem(
                text_en="Due soon: Assignment",
                text_zh="尽快安排：作业",
                due_at=None,
                source_ids=["t1"],
                action_url=None,
            )
        ],
    )

    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=config)

    # decaying concepts appear with mastery-aware labels
    assert any("Review (critical)" in item.text_en for item in base.todo), (
        "critical decaying concept should appear with urgency label"
    )
    assert any("bode plot" in item.text_en.lower() for item in base.todo)
    assert any("15%" in item.text_en for item in base.todo), (
        "mastery percentage should appear in review item"
    )
    assert any("Review (fading)" in item.text_en for item in base.todo), (
        "decaying concept should appear with fading label"
    )
    assert any("transient response" in item.text_en.lower() for item in base.todo)
    assert any("35%" in item.text_en for item in base.todo)
    # Knowledge query still works
    assert any("Review note from" in item.text_en for item in base.todo)


def test_inject_learning_context_handles_query_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing_module, "MemoryAPI", lambda store: _MemoryAPIStub())

    def _boom(*args, **kwargs):
        raise RuntimeError("chroma unavailable")

    monkeypatch.setattr(briefing_module, "query_knowledge", _boom)

    config = type("C", (), {"data_dir": tmp_path})()
    base = Briefing(todo=[], schedule=[], warnings=[], task_index={}, focus=[])
    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=config)

    assert any("Knowledge query unavailable" in warning for warning in base.warnings)
    # decaying concepts still injected even when knowledge query fails
    assert any("bode plot" in item.text_en.lower() for item in base.todo)


def test_b1_decaying_concepts_ordered_by_urgency(monkeypatch, tmp_path):
    """Critical concepts appear before decaying ones in todo."""
    monkeypatch.setattr(briefing_module, "MemoryAPI", lambda store: _MemoryAPIStub())
    monkeypatch.setattr(briefing_module, "query_knowledge", lambda *args, **kwargs: _SummaryStub())

    config = type("C", (), {"data_dir": tmp_path})()
    base = Briefing(todo=[], schedule=[], warnings=[], task_index={}, focus=[])
    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=config)

    # Find the review items (not knowledge note items)
    review_items = [
        item for item in base.todo
        if "Review" in item.text_en and "Review note" not in item.text_en
    ]
    # Critical ("bode plot" at 15%) should come before decaying ("transient response" at 35%)
    assert len(review_items) >= 2
    assert "critical" in review_items[0].text_en.lower()
    assert "bode plot" in review_items[0].text_en.lower()


def test_b1_weak_point_fallback_when_no_decaying(monkeypatch, tmp_path):
    """Falls back to weak_points when TemporalMemory has no decaying concepts
    and low_mastery_topics are also empty."""

    class _EmptyMemoryAPIStub:
        def get_memory_context(self):
            ctx = type("Ctx", (), {})()
            ctx.weak_points = ["op-amp feedback", "kirchhoff"]
            ctx.review_queue = []
            ctx.low_mastery_topics = []
            ctx.context_topics = []
            return ctx

        def get_decaying_concepts(self, threshold: float = 0.4):
            return []

    monkeypatch.setattr(briefing_module, "MemoryAPI", lambda store: _EmptyMemoryAPIStub())
    monkeypatch.setattr(briefing_module, "query_knowledge", lambda *args, **kwargs: _SummaryStub())

    config = type("C", (), {"data_dir": tmp_path})()
    base = Briefing(todo=[], schedule=[], warnings=[], task_index={}, focus=[])
    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=config)

    # Should fall back to weak_points: "op-amp feedback", "kirchhoff"
    review_items = [
        item for item in base.todo
        if "Review" in item.text_en and "Review note" not in item.text_en
    ]
    assert len(review_items) >= 1
    assert any(
        "op-amp feedback" in item.text_en.lower()
        or "kirchhoff" in item.text_en.lower()
        for item in review_items
    )
