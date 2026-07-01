"""B2: Tests for _llm_briefing exception/fallback → _rule_briefing safety net.

Verifies that build_briefing never raises when LLM fails, and that
appropriate warnings are captured in the briefing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nexus.intelligence.briefing import (
    Briefing,
    BriefingItem,
    build_briefing,
)
from nexus.intelligence.llm import LLMError
from nexus.models import Task


# ── helpers ──────────────────────────────────────────────────────────

def _make_task(
    id: str = "t1",
    title: str = "Homework 3",
    due_at: datetime | None = None,
    source: str = "brightspace",
    tags: list[str] | None = None,
) -> Task:
    return Task(
        id=id,
        title=title,
        due_at=due_at,
        source=source,
        tags=tags or [],
    )


def _task_in_window() -> Task:
    """Task due tomorrow — always within default 7-day window."""
    return _make_task(
        id="fb1",
        title="Midterm Review",
        due_at=datetime.now().astimezone() + timedelta(days=1),
        source="brightspace",
    )


def _llm_enabled_config(tmp_path: Path):
    """Config with llm_enabled=True and data_dir pointing to tmp_path
    so _inject_learning_context doesn't touch real data/nexus.db."""
    return type("C", (), {
        "llm_enabled": True,
        "llm_base_url": "http://mock",
        "llm_api_key": "mock-key",
        "llm_model": "mock-model",
        "llm_timeout": 30,
        "llm_json_output": False,
        "data_dir": tmp_path,
    })()


def _noop_inject(briefing, now_local, config):
    """Stub that prevents _inject_learning_context from touching the DB."""
    pass


# ── tests ────────────────────────────────────────────────────────────

class TestBuildBriefingFallback:
    """B2: _llm_briefing failures → _rule_briefing with warnings intact."""

    def test_llm_error_falls_back_to_rule(self, monkeypatch, tmp_path):
        """LLMError in _llm_briefing → _rule_briefing used, warning captured."""
        from nexus.intelligence import briefing as briefing_module

        monkeypatch.setattr(briefing_module, "_inject_learning_context", _noop_inject)
        monkeypatch.setattr(
            briefing_module,
            "_llm_briefing",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                LLMError("DashScope timeout")
            ),
        )

        task = _task_in_window()
        config = _llm_enabled_config(tmp_path)
        result = build_briefing(
            [task],
            window_days=7,
            now=datetime.now().astimezone(),
            config=config,
            use_llm=True,
        )

        assert isinstance(result, Briefing)
        assert len(result.warnings) >= 1
        assert any("DashScope timeout" in w for w in result.warnings)
        # fallback still produces schedule (task has due_at)
        assert len(result.schedule) >= 1

    def test_generic_exception_falls_back_to_rule(self, monkeypatch, tmp_path):
        """Non-LLMError exception in _llm_briefing → _rule_briefing, warning captured."""
        from nexus.intelligence import briefing as briefing_module

        monkeypatch.setattr(briefing_module, "_inject_learning_context", _noop_inject)
        monkeypatch.setattr(
            briefing_module,
            "_llm_briefing",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("network gone")
            ),
        )

        task = _task_in_window()
        config = _llm_enabled_config(tmp_path)
        result = build_briefing(
            [task],
            window_days=7,
            now=datetime.now().astimezone(),
            config=config,
            use_llm=True,
        )

        assert isinstance(result, Briefing)
        assert any("LLM parse failed" in w for w in result.warnings)
        assert any("network gone" in w for w in result.warnings)
        # rule-based fallback still works
        assert len(result.schedule) >= 1

    def test_llm_not_configured_falls_back(self, monkeypatch, tmp_path):
        """When LLM is not configured, skip _llm_briefing, warn, rule-based fallback."""
        from nexus.intelligence import briefing as briefing_module

        monkeypatch.setattr(briefing_module, "_inject_learning_context", _noop_inject)

        task = _task_in_window()
        # llm_enabled=True but is_configured returns False (no base_url/model in config)
        config = type("C", (), {
            "llm_enabled": True,
            "llm_base_url": "",
            "llm_api_key": "",
            "llm_model": "",
            "llm_timeout": 30,
            "llm_json_output": False,
            "data_dir": tmp_path,
        })()
        result = build_briefing(
            [task],
            window_days=7,
            now=datetime.now().astimezone(),
            config=config,
            use_llm=True,
        )

        assert isinstance(result, Briefing)
        assert any("LLM not configured" in w for w in result.warnings)
        # rule-based fallback still works
        assert len(result.schedule) >= 1

    def test_use_llm_false_skips_llm_entirely(self, monkeypatch, tmp_path):
        """use_llm=False → no LLM call, no warning, straight to rule."""
        monkeypatch.setattr(
            "nexus.intelligence.briefing._inject_learning_context",
            _noop_inject,
        )

        task = _task_in_window()
        config = _llm_enabled_config(tmp_path)
        result = build_briefing(
            [task],
            window_days=7,
            now=datetime.now().astimezone(),
            config=config,
            use_llm=False,
        )

        assert isinstance(result, Briefing)
        # No LLM-related warning when use_llm=False
        llm_warnings = [
            w for w in result.warnings
            if "LLM" in w or "llm" in w.lower()
        ]
        assert len(llm_warnings) == 0
        assert len(result.schedule) >= 1

    def test_fallback_preserves_focus_and_warm_message(self, monkeypatch, tmp_path):
        """Fallback briefing still gets warm_message and focus items."""
        from nexus.intelligence import briefing as briefing_module

        monkeypatch.setattr(briefing_module, "_inject_learning_context", _noop_inject)
        monkeypatch.setattr(
            briefing_module,
            "_llm_briefing",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                LLMError("boom")
            ),
        )

        task = _task_in_window()
        config = _llm_enabled_config(tmp_path)
        result = build_briefing(
            [task],
            window_days=7,
            now=datetime.now().astimezone(),
            config=config,
            use_llm=True,
        )

        assert result.warm_message is not None
        assert len(result.warm_message) > 0
        # Focus items should be populated when schedule has items
        assert len(result.focus) >= 1

    def test_fallback_with_empty_tasks_produces_empty_briefing(self, monkeypatch, tmp_path):
        """Fallback with no tasks → empty briefing, not a crash."""
        from nexus.intelligence import briefing as briefing_module

        monkeypatch.setattr(briefing_module, "_inject_learning_context", _noop_inject)
        monkeypatch.setattr(
            briefing_module,
            "_llm_briefing",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                LLMError("boom")
            ),
        )

        config = _llm_enabled_config(tmp_path)
        result = build_briefing(
            [],
            window_days=7,
            now=datetime.now().astimezone(),
            config=config,
            use_llm=True,
        )

        assert isinstance(result, Briefing)
        assert len(result.todo) == 0
        assert len(result.schedule) == 0
        assert len(result.warnings) >= 1
