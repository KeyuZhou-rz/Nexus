from __future__ import annotations

import pytest

from nexus.knowledge.concept_resolver import ConceptResolver
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.temporal_memory import TemporalMemory
from nexus.knowledge.user_profile import UserProfileManager
from nexus.memory_writer import apply_feedback_unified, record_weak_point


COURSE = "CS202_OS"
CONCEPT_ID = "cs202_os__deadlock"


def _seed_concept(store: SQLiteStore) -> None:
    store.upsert_concept(
        concept_id=CONCEPT_ID,
        name="Deadlock",
        course_id=COURSE,
        definition="A situation where processes wait indefinitely for resources held by each other.",
        concept_type="fundamental",
    )
    store.upsert_alias(
        alias="deadlock",
        course_id=COURSE,
        concept_id=CONCEPT_ID,
        source="manual",
        confidence=1.0,
    )


def _seed_extra_concept(store: SQLiteStore) -> None:
    store.upsert_concept(
        concept_id="cs202_os__mutex",
        name="Mutex",
        course_id=COURSE,
        definition="Mutual exclusion primitive.",
        concept_type="fundamental",
    )


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "nexus.db"
    s = SQLiteStore(db_path)
    _seed_concept(s)
    _seed_extra_concept(s)
    return s


@pytest.fixture
def resolver(store):
    return ConceptResolver(store)


@pytest.fixture
def profile_mgr(store):
    return UserProfileManager(store)


@pytest.fixture
def temporal(store):
    return TemporalMemory(store)


# ─────────────────────────────────────────────────────
# record_weak_point
# ─────────────────────────────────────────────────────
class TestRecordWeakPointResolved:
    def test_adds_weak_point_and_temporal_event(self, resolver, profile_mgr, temporal):
        result = record_weak_point(resolver, profile_mgr, temporal, "deadlock", COURSE)

        assert result["resolved"] is True
        assert result["concept_id"] == CONCEPT_ID
        assert isinstance(result["level"], int)

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "Deadlock" in weak_concepts

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(events) == 1
        assert events[0]["event_type"] == "struggled"

    def test_updates_last_seen_on_duplicate(self, resolver, profile_mgr, temporal):
        record_weak_point(resolver, profile_mgr, temporal, "deadlock", COURSE)
        record_weak_point(resolver, profile_mgr, temporal, "deadlock", COURSE)

        profile = profile_mgr.get()
        weak_points = [wp for wp in profile["weak_points"] if wp["concept"] == "Deadlock"]
        assert len(weak_points) == 1

    def test_session_id_and_context_passed_to_temporal(self, resolver, profile_mgr, temporal):
        ctx = {"source": "quiz", "question": "Define deadlock"}
        record_weak_point(
            resolver, profile_mgr, temporal, "deadlock", COURSE,
            session_id="sess-1", context=ctx,
        )

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert events[0]["session_id"] == "sess-1"
        import json
        assert json.loads(events[0]["context"]) == ctx


class TestRecordWeakPointFallback:
    def test_unresolvable_name_still_adds_weak_point(self, resolver, profile_mgr, temporal):
        result = record_weak_point(resolver, profile_mgr, temporal, "QuantumFooBar", COURSE)

        assert result["resolved"] is False
        assert result["concept_id"] is None
        assert result["level"] is None

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "QuantumFooBar" in weak_concepts

    def test_unresolvable_name_no_temporal_event(self, resolver, profile_mgr, temporal):
        record_weak_point(resolver, profile_mgr, temporal, "QuantumFooBar", COURSE)

        all_events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(all_events) == 0


class TestRecordWeakPointEmpty:
    def test_empty_string_resolves_false(self, resolver, profile_mgr, temporal):
        result = record_weak_point(resolver, profile_mgr, temporal, "", COURSE)

        assert result["resolved"] is False
        assert result["concept_id"] is None
        assert result["level"] is None

    def test_empty_string_still_adds_weak_point(self, resolver, profile_mgr, temporal):
        record_weak_point(resolver, profile_mgr, temporal, "", COURSE)

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "" in weak_concepts

    def test_whitespace_only(self, resolver, profile_mgr, temporal):
        result = record_weak_point(resolver, profile_mgr, temporal, "   ", COURSE)

        assert result["resolved"] is False


class TestRecordWeakPointNormMatch:
    def test_normalized_match_level_2(self, resolver, profile_mgr, temporal):
        store = resolver._store
        store.upsert_concept(
            concept_id="cs202_os__page_fault",
            name="Page Fault Handling",
            course_id=COURSE,
        )
        result = record_weak_point(resolver, profile_mgr, temporal, "page fault handling", COURSE)

        assert result["resolved"] is True
        assert result["concept_id"] == "cs202_os__page_fault"
        assert result["level"] == 2

    def test_fuzzy_match_level_3(self, resolver, profile_mgr, temporal):
        store = resolver._store
        store.upsert_concept(
            concept_id="cs202_os__cache_coherence",
            name="Cache Coherence",
            course_id=COURSE,
        )
        result = record_weak_point(resolver, profile_mgr, temporal, "cache coherency", COURSE)

        assert result["resolved"] is True
        assert result["level"] == 3


# ─────────────────────────────────────────────────────
# apply_feedback_unified
# ─────────────────────────────────────────────────────


class TestApplyFeedbackAcceptResolved:
    def test_mastered_and_correct_answer_event(self, resolver, profile_mgr, temporal):
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "deadlock", COURSE, "accept")

        assert result["action"] == "accept"
        assert result["resolved"] is True
        assert result["concept_id"] == CONCEPT_ID

        profile = profile_mgr.get()
        mastered_concepts = [m["concept"] for m in profile["mastered"]]
        assert "Deadlock" in mastered_concepts

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(events) == 1
        assert events[0]["event_type"] == "correct_answer"

    def test_accept_also_removes_weak_point(self, resolver, profile_mgr, temporal):
        profile_mgr.add_weak_point("Deadlock", COURSE)
        apply_feedback_unified(resolver, profile_mgr, temporal, "deadlock", COURSE, "accept")

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "Deadlock" not in weak_concepts


class TestApplyFeedbackRejectResolved:
    def test_weak_point_removed_and_forgot_event(self, resolver, profile_mgr, temporal):
        profile_mgr.add_weak_point("Deadlock", COURSE)
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "deadlock", COURSE, "reject")

        assert result["action"] == "reject"
        assert result["resolved"] is True
        assert result["concept_id"] == CONCEPT_ID

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "Deadlock" not in weak_concepts

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(events) == 1
        assert events[0]["event_type"] == "forgot"


class TestApplyFeedbackRejectIntegration:
    """Full call chain: record_weak_point -> apply_feedback_unified reject."""

    def test_reject_removes_weak_point_via_resolved_name(self, resolver, profile_mgr, temporal):
        record_weak_point(resolver, profile_mgr, temporal, "deadlock", COURSE)
        apply_feedback_unified(resolver, profile_mgr, temporal, "deadlock", COURSE, "reject")

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "Deadlock" not in weak_concepts

    def test_reject_unresolved_removes_by_original_name(self, resolver, profile_mgr, temporal):
        record_weak_point(resolver, profile_mgr, temporal, "SomeUnknownThing", COURSE)
        apply_feedback_unified(resolver, profile_mgr, temporal, "SomeUnknownThing", COURSE, "reject")

        profile = profile_mgr.get()
        weak_concepts = [wp["concept"] for wp in profile["weak_points"]]
        assert "SomeUnknownThing" not in weak_concepts


class TestApplyFeedbackFallback:
    def test_accept_unresolved_adds_mastered_no_temporal(self, resolver, profile_mgr, temporal):
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "UnknownThing", COURSE, "accept")

        assert result["resolved"] is False
        assert result["concept_id"] is None

        profile = profile_mgr.get()
        mastered = [m["concept"] for m in profile["mastered"]]
        assert "UnknownThing" in mastered

        all_events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(all_events) == 0

    def test_reject_unresolved_removes_weak_point_no_temporal(self, resolver, profile_mgr, temporal):
        profile_mgr.add_weak_point("UnknownThing", COURSE)
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "UnknownThing", COURSE, "reject")

        assert result["resolved"] is False
        assert result["concept_id"] is None

        profile = profile_mgr.get()
        weak = [wp["concept"] for wp in profile["weak_points"]]
        assert "UnknownThing" not in weak

        all_events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(all_events) == 0


class TestApplyFeedbackEdgeCases:
    def test_empty_concept_name(self, resolver, profile_mgr, temporal):
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "", COURSE, "accept")

        assert result["resolved"] is False
        assert result["concept_id"] is None
        assert result["action"] == "accept"

    def test_unknown_action_no_crash(self, resolver, profile_mgr, temporal):
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "deadlock", COURSE, "bogus")

        assert result["action"] == "bogus"
        assert result["resolved"] is True
        assert result["concept_id"] == CONCEPT_ID

    def test_unknown_action_unresolved_no_crash(self, resolver, profile_mgr, temporal):
        result = apply_feedback_unified(resolver, profile_mgr, temporal, "Unknown", COURSE, "bogus")

        assert result["action"] == "bogus"
        assert result["resolved"] is False
        assert result["concept_id"] is None
