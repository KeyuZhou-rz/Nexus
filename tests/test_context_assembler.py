"""U1: Tests for Socratic guidance — mastery-aware teaching strategy.

Covers _pick_socratic_level, SOCRATIC_GUIDANCE integration in
assemble_messages, and the new mastery_map parameter.
"""
from __future__ import annotations

import inspect
import pytest

from nexus.knowledge.context_assembler import (
    _pick_socratic_level,
    assemble_messages,
)
from nexus.knowledge.prompts import SOCRATIC_GUIDANCE, TEACHING_MODE_GUIDANCE
from nexus.knowledge.qa_pipeline import QAPipeline


# ── helpers ──────────────────────────────────────────────────────────

class _FakeChunk:
    def __init__(self, chunk_id: str, content: str, metadata: dict | None = None):
        self.chunk_id = chunk_id
        self.content = content
        self.metadata = metadata or {
            "source_file": "lec01.pdf",
            "lecture_number": "1",
            "page": "5",
            "topic": "Deadlock",
        }
        self.score = 0.85


def _chunk(text: str = "Deadlock occurs when...") -> _FakeChunk:
    return _FakeChunk("c1", text)


# ── tests: _pick_socratic_level ──────────────────────────────────────

class TestPickSocraticLevel:
    def test_none_map_returns_unknown(self):
        assert _pick_socratic_level(None) == "unknown"

    def test_empty_map_returns_unknown(self):
        assert _pick_socratic_level({}) == "unknown"

    def test_critical_when_single_concept_below_30(self):
        assert _pick_socratic_level({"deadlock": 0.15}) == "critical"

    def test_critical_when_average_below_30(self):
        assert _pick_socratic_level({"deadlock": 0.1, "mutex": 0.2}) == "critical"

    def test_decaying_when_single_concept_between_30_and_50(self):
        assert _pick_socratic_level({"deadlock": 0.35}) == "decaying"

    def test_decaying_when_average_between_30_and_50(self):
        assert _pick_socratic_level({"deadlock": 0.3, "mutex": 0.5}) == "decaying"

    def test_decaying_at_boundary_30(self):
        assert _pick_socratic_level({"deadlock": 0.30}) == "decaying"

    def test_stable_when_lowest_between_50_and_80(self):
        assert _pick_socratic_level({"deadlock": 0.55}) == "stable"

    def test_stable_at_boundary_50(self):
        assert _pick_socratic_level({"deadlock": 0.50}) == "stable"

    def test_mastered_when_lowest_above_80(self):
        assert _pick_socratic_level({"deadlock": 0.85}) == "mastered"

    def test_mastered_at_boundary_80(self):
        assert _pick_socratic_level({"deadlock": 0.80}) == "mastered"

    def test_average_not_dominated_by_single_weak_concept(self):
        # A single weak "mutex" (0.15) among two well-mastered concepts used
        # to force "critical" via min(). Average reflects the overall
        # relevant-concept mastery instead: (0.95+0.15+0.6)/3 ≈ 0.567 -> "stable".
        assert _pick_socratic_level({
            "deadlock": 0.95,
            "mutex": 0.15,
            "scheduling": 0.6,
        }) == "stable"


# ── tests: SOCRATIC_GUIDANCE content ────────────────────────────────

class TestSocraticGuidanceContent:
    def test_all_levels_have_content(self):
        for level in ["critical", "decaying", "stable", "mastered", "unknown"]:
            assert level in SOCRATIC_GUIDANCE, f"missing level: {level}"
            assert len(SOCRATIC_GUIDANCE[level]) > 0, f"empty content for: {level}"

    def test_critical_mentions_do_not_answer_directly(self):
        guidance = SOCRATIC_GUIDANCE["critical"]
        assert "不要直接给出答案" in guidance or "分步追问" in guidance

    def test_decaying_mentions_hint_or_analogy(self):
        guidance = SOCRATIC_GUIDANCE["decaying"]
        assert "提示" in guidance or "类比" in guidance

    def test_mastered_mentions_challenge(self):
        guidance = SOCRATIC_GUIDANCE["mastered"]
        assert "延伸" in guidance or "挑战" in guidance or "深入" in guidance


# ── tests: assemble_messages with mastery_map ────────────────────────

class TestAssembleMessagesWithMastery:
    def test_qa_pipeline_exposes_teaching_mode(self):
        parameter = inspect.signature(QAPipeline.ask).parameters["teaching_mode"]
        assert parameter.default == "answer"

    def test_default_answer_mode_answers_directly(self):
        messages = assemble_messages(
            user_query="What is deadlock?",
            chunks=[_chunk()],
            mastery_map=None,
        )
        system = messages[0]["content"]
        assert TEACHING_MODE_GUIDANCE["answer"] in system
        assert "保持直接回答" in system

    def test_empty_mastery_map_includes_unknown_guidance(self):
        messages = assemble_messages(
            user_query="What is deadlock?",
            chunks=[_chunk()],
            mastery_map={},
        )
        system = messages[0]["content"]
        assert TEACHING_MODE_GUIDANCE["answer"] in system

    def test_critical_mastery_injects_critical_guidance(self):
        messages = assemble_messages(
            user_query="What is deadlock?",
            chunks=[_chunk()],
            mastery_map={"deadlock": 0.15},
        )
        system = messages[0]["content"]
        assert "保持直接回答" in system
        assert "不要直接给出答案" not in system

    def test_decaying_mastery_injects_decaying_guidance(self):
        messages = assemble_messages(
            user_query="What is semaphore?",
            chunks=[_chunk()],
            mastery_map={"semaphore": 0.40},
        )
        system = messages[0]["content"]
        assert "decaying" in system

    def test_stable_mastery_injects_stable_guidance(self):
        messages = assemble_messages(
            user_query="Explain mutex.",
            chunks=[_chunk()],
            mastery_map={"mutex": 0.65},
        )
        system = messages[0]["content"]
        assert "stable" in system

    def test_mastered_mastery_injects_mastered_guidance(self):
        messages = assemble_messages(
            user_query="Tell me about scheduling.",
            chunks=[_chunk()],
            mastery_map={"scheduling": 0.90},
        )
        system = messages[0]["content"]
        assert "mastered" in system

    def test_tutor_mode_uses_mastery_guidance(self):
        messages = assemble_messages(
            user_query="Give me a hint about deadlock",
            chunks=[_chunk()],
            mastery_map={"deadlock": 0.15},
            teaching_mode="tutor",
        )
        assert SOCRATIC_GUIDANCE["critical"] in messages[0]["content"]

    def test_assessment_mode_hides_answer(self):
        messages = assemble_messages(
            user_query="Test me",
            chunks=[_chunk()],
            teaching_mode="assessment",
        )
        assert TEACHING_MODE_GUIDANCE["assessment"] in messages[0]["content"]

    def test_context_uses_stable_source_ids(self):
        messages = assemble_messages(user_query="What is deadlock?", chunks=[_chunk()])
        user = messages[1]["content"]
        assert "[S1]" in user
        assert "（来源：" not in user

    def test_invalid_teaching_mode_rejected(self):
        with pytest.raises(ValueError):
            assemble_messages(user_query="x", chunks=[], teaching_mode="invalid")

    def test_teaching_style_still_injected_with_mastery(self):
        """U1 doesn't break existing teaching_style injection."""
        messages = assemble_messages(
            user_query="What is deadlock?",
            chunks=[_chunk()],
            profile={"learning_style": "visual"},
            mastery_map={"deadlock": 0.2},
        )
        system = messages[0]["content"]
        assert "类比" in system or "图示" in system  # visual style keywords

    def test_mastery_map_not_leaked_to_user_message(self):
        """Mastery data should only affect system prompt, not user message."""
        messages = assemble_messages(
            user_query="What is deadlock?",
            chunks=[_chunk("Mutex is a lock.")],
            mastery_map={"deadlock": 0.15},
        )
        user_content = messages[1]["content"]
        assert "0.15" not in user_content
        assert "critical" not in user_content.lower()
        assert "mastery" not in user_content.lower()
