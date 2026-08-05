"""Tests for Phase N4 MemoryAPI facade."""
from __future__ import annotations

from pathlib import Path

import pytest

from nexus.knowledge.memory_api import (
    ForgettingAlert,
    MemoryAPI,
    MemoryContext,
    MasteryOverview,
)
from nexus.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def memory_api(tmp_path: Path) -> MemoryAPI:
    """Create a MemoryAPI backed by a fresh SQLiteStore with sample concepts."""
    store = SQLiteStore(tmp_path / "test.db")
    store.upsert_concept("cs202_os__deadlock", "Deadlock", "CS202_OS")
    store.upsert_concept("cs202_os__mutex", "Mutex", "CS202_OS")
    store.upsert_concept("db__txn", "Transaction", "DB101")
    return MemoryAPI(store)


def test_get_mastery_returns_zero_for_unknown(memory_api: MemoryAPI):
    assert memory_api.get_mastery("unknown__concept") == 0.0


def test_record_event_and_get_mastery(memory_api: MemoryAPI):
    snap = memory_api.record_event(
        "cs202_os__deadlock",
        "correct_answer",
        session_id="s1",
        context={"evidence_type": "assessment"},
    )
    assert snap is not None
    assert snap.concept_id == "cs202_os__deadlock"
    assert snap.current_mastery == pytest.approx(0.15)

    mastery = memory_api.get_mastery("cs202_os__deadlock")
    assert mastery == pytest.approx(0.15, abs=0.01)


def test_get_mastery_overview_and_to_dict(memory_api: MemoryAPI):
    # Build deadlock up to mastered (>=0.8) with repeated correct answers.
    for _ in range(6):
        memory_api.record_event(
            "cs202_os__deadlock", "correct_answer",
            context={"evidence_type": "assessment"},
        )
    memory_api.record_event(
        "cs202_os__mutex", "struggled",
        context={"evidence_type": "assessment"},
    )

    overview = memory_api.get_mastery_overview("CS202_OS")
    assert isinstance(overview, MasteryOverview)
    assert overview.course_id == "CS202_OS"
    assert overview.total_concepts == 2
    assert overview.mastered >= 1
    assert overview.critical >= 1

    d = overview.to_dict()
    assert d["course_id"] == "CS202_OS"
    assert d["total_concepts"] == 2
    assert 0.0 <= d["average_mastery"] <= 1.0
    assert len(d["concepts"]) == 2
    concept = d["concepts"][0]
    assert "concept_id" in concept
    assert "concept_name" in concept
    assert "course_id" in concept
    assert "current_mastery" in concept
    assert "days_since_review" in concept
    assert "urgency" in concept
    assert "review_count" in concept
    assert "decay_rate" in concept
    assert "initial_mastery" in concept


def test_get_decaying_concepts(memory_api: MemoryAPI):
    memory_api.record_event(
        "cs202_os__deadlock", "forgot",
        context={"evidence_type": "assessment"},
    )
    memory_api.record_event(
        "cs202_os__mutex", "correct_answer",
        context={"evidence_type": "assessment"},
    )

    decaying = memory_api.get_decaying_concepts(threshold=0.4, course_id="CS202_OS")
    assert isinstance(decaying, list)
    assert all(isinstance(s.concept_name, str) for s in decaying)
    names = {s.concept_name for s in decaying}
    assert "Deadlock" in names


def test_get_forgetting_alerts(memory_api: MemoryAPI):
    memory_api.record_event(
        "cs202_os__deadlock", "correct_answer",
        context={"evidence_type": "assessment"},
    )
    alerts = memory_api.get_forgetting_alerts("CS202_OS")
    assert isinstance(alerts, list)
    for alert in alerts:
        assert isinstance(alert, ForgettingAlert)
        assert 0.0 <= alert.current_mastery <= 1.0
        assert alert.days_until_critical >= 0.0
        d = alert.to_dict()
        assert "concept_id" in d
        assert "concept_name" in d
        assert "current_mastery" in d
        assert "days_until_critical" in d
        assert "urgency" in d


def test_profile_methods(memory_api: MemoryAPI):
    memory_api.add_course("CS202_OS")
    memory_api.add_course("DB101")

    profile = memory_api.get_profile()
    assert isinstance(profile, dict)
    assert "courses" in profile
    assert set(memory_api.get_courses()) == {"CS202_OS", "DB101"}
    assert memory_api.get_learning_style() in {"visual", "textual", "example-driven", "default"}
    assert isinstance(memory_api.get_weak_points(), list)


def test_apply_profile_patch(memory_api: MemoryAPI):
    memory_api.add_course("CS202_OS")
    patch = {
        "update_learning_style": "visual",
        "add_common_mistakes": ["confused mutex with semaphore"],
        "notes": "test patch",
    }
    result = memory_api.apply_profile_patch(patch, course_id="CS202_OS", session_id="s1")
    assert isinstance(result, dict)
    assert "applied" in result
    assert memory_api.get_learning_style() == "visual"


def test_generate_study_plan(memory_api: MemoryAPI):
    memory_api.record_event(
        "cs202_os__deadlock", "correct_answer",
        context={"evidence_type": "assessment"},
    )
    plan = memory_api.generate_study_plan("CS202_OS", num_days=3)
    assert isinstance(plan, dict)
    assert "plan" in plan
    assert isinstance(plan["plan"], list)


def test_get_memory_context_and_context_topics(memory_api: MemoryAPI):
    memory_api.record_event(
        "cs202_os__deadlock", "forgot",
        context={"evidence_type": "assessment"},
    )

    topics = memory_api.get_context_topics()
    assert isinstance(topics, list)
    assert all(isinstance(t, str) for t in topics)
    assert len(topics) <= 5

    ctx = memory_api.get_memory_context()
    assert isinstance(ctx, MemoryContext)
    assert isinstance(ctx.weak_points, list)
    assert isinstance(ctx.review_queue, list)
    assert isinstance(ctx.low_mastery_topics, list)
    assert isinstance(ctx.context_topics, list)

    d = ctx.to_dict()
    assert "weak_points" in d
    assert "review_queue" in d
    assert "low_mastery_topics" in d
    assert "context_topics" in d


def test_evidence_summary_exposes_source_time_and_trust_without_question_text(
    memory_api: MemoryAPI,
) -> None:
    memory_api.record_event(
        "cs202_os__deadlock",
        "struggled",
        session_id="session-1",
        context={
            "evidence_type": "assessment",
            "source": "offline_quiz",
            "confidence": 0.82,
            "question": "Sensitive question text",
        },
    )

    evidence = memory_api.get_evidence_summary("CS202_OS")

    item = next(entry for entry in evidence if entry["concept_id"] == "cs202_os__deadlock")
    assert item["source"] == "offline_quiz"
    assert item["trust_status"] == "trusted"
    assert item["confidence"] == 0.82
    assert item["timestamp"]
    assert "question" not in item
