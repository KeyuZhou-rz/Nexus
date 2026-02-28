from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nexus.intelligence import briefing as briefing_module
from nexus.intelligence.briefing import Briefing, BriefingItem, _inject_learning_context


class _StateStub:
    weak_points = ["op-amp feedback", "kirchhoff"]


class _SummaryStub:
    class _Item:
        def __init__(self, text: str, file_name: str):
            self.text = text
            self.metadata = {"file_name": file_name}
            self.distance = 0.1

    items = [_Item("Negative feedback stabilizes gain.", "lecture_05.md")]


class _ConfigStub:
    data_dir = Path("data")


def test_inject_learning_context_appends_items(monkeypatch):
    monkeypatch.setattr(briefing_module, "load_state", lambda _path: _StateStub())
    monkeypatch.setattr(briefing_module, "query_knowledge", lambda *args, **kwargs: _SummaryStub())

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

    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=_ConfigStub())

    assert any("Review weak point" in item.text_en for item in base.todo)
    assert any("Review note from" in item.text_en for item in base.todo)


def test_inject_learning_context_handles_query_failure(monkeypatch):
    monkeypatch.setattr(briefing_module, "load_state", lambda _path: _StateStub())

    def _boom(*args, **kwargs):
        raise RuntimeError("chroma unavailable")

    monkeypatch.setattr(briefing_module, "query_knowledge", _boom)

    base = Briefing(todo=[], schedule=[], warnings=[], task_index={}, focus=[])
    _inject_learning_context(base, now_local=datetime.now().astimezone(), config=_ConfigStub())

    assert any("Knowledge query unavailable" in warning for warning in base.warnings)
