from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nexus.intelligence import briefing as briefing_module
from nexus.intelligence.briefing import Briefing, BriefingItem, _inject_learning_context
from nexus.knowledge.unified_memory import UnifiedMemoryReader


class _MemoryReaderStub:
    """Mimics UnifiedMemoryReader interface."""

    def get_weak_points(self) -> list[str]:
        return ["op-amp feedback", "kirchhoff"]

    def get_review_queue(self, threshold: float = 0.4) -> list[str]:
        return ["bode plot"]

    def get_low_mastery_topics(self, max_mastery: float = 0.5) -> list[str]:
        return ["transient response"]


class _SummaryStub:
    class _Item:
        def __init__(self, text: str, file_name: str):
            self.text = text
            self.metadata = {"file_name": file_name}
            self.distance = 0.1

    items = [_Item("Negative feedback stabilizes gain.", "lecture_05.md")]


class _ConfigStub:
    data_dir = Path("data")


def _mock_unified_reader(store):
    return _MemoryReaderStub()


def test_inject_learning_context_appends_items(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing_module, "UnifiedMemoryReader", _mock_unified_reader)
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

    assert any("Review weak point" in item.text_en for item in base.todo)
    assert any("bode plot" in item.text_en.lower() for item in base.todo)
    assert any("transient response" in item.text_en.lower() for item in base.todo)
    assert any("Review note from" in item.text_en for item in base.todo)


def test_inject_learning_context_handles_query_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing_module, "UnifiedMemoryReader", _mock_unified_reader)

    def _boom(*args, **kwargs):
        raise RuntimeError("chroma unavailable")

    monkeypatch.setattr(briefing_module, "query_knowledge", _boom)

    config = type("C", (), {"data_dir": tmp_path})()
    base = Briefing(todo=[], schedule=[], warnings=[], task_index={}, focus=[])
    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=config)

    assert any("Knowledge query unavailable" in warning for warning in base.warnings)
