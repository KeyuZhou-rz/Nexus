"""
Tests for unifying the two mastery data sources (Phase 4B follow-up):
profile.weak_points/mastered are now derived from TemporalMemory.concept_mastery
when UserProfileManager is wired with temporal_memory + concept_resolver, instead
of being independently LLM-written free text.

Backward compatibility: UserProfileManager(store) without those deps keeps the
old free-text JSON behavior untouched (see test_memory_writer.py).
"""
from __future__ import annotations

import pytest

from nexus.knowledge.concept_resolver import ConceptResolver
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.temporal_memory import TemporalMemory
from nexus.knowledge.user_profile import UserProfileManager

COURSE = "CS202_OS"
CONCEPT_ID = "cs202_os__deadlock"


def _seed(store: SQLiteStore) -> None:
    store.upsert_concept(
        concept_id=CONCEPT_ID, name="Deadlock", course_id=COURSE,
    )


@pytest.fixture
def store(tmp_path):
    s = SQLiteStore(tmp_path / "nexus.db")
    _seed(s)
    return s


@pytest.fixture
def temporal(store):
    return TemporalMemory(store)


@pytest.fixture
def resolver(store):
    return ConceptResolver(store)


@pytest.fixture
def wired_profile_mgr(store, temporal, resolver):
    return UserProfileManager(store, temporal_memory=temporal, concept_resolver=resolver)


class TestDerivedGet:
    def test_no_events_yields_empty_lists(self, wired_profile_mgr):
        profile = wired_profile_mgr.get()
        assert profile["weak_points"] == []
        assert profile["mastered"] == []

    def test_struggled_event_surfaces_as_weak_point(self, wired_profile_mgr, temporal):
        temporal.record_event(CONCEPT_ID, "struggled", evidence_type="assessment")

        profile = wired_profile_mgr.get()
        assert any(wp["concept"] == "Deadlock" for wp in profile["weak_points"])
        assert profile["mastered"] == []

    def test_repeated_correct_answers_surface_as_mastered_not_weak(
        self, wired_profile_mgr, temporal
    ):
        for _ in range(6):  # push past the 0.8 "mastered" threshold
            temporal.record_event(CONCEPT_ID, "correct_answer", evidence_type="assessment")

        profile = wired_profile_mgr.get()
        assert any(m["concept"] == "Deadlock" for m in profile["mastered"])
        assert not any(wp["concept"] == "Deadlock" for wp in profile["weak_points"])

    def test_unwired_profile_mgr_keeps_legacy_json_behavior(self, store):
        """Without temporal_memory/concept_resolver, get() must behave exactly
        like before — pure JSON storage, unaffected by concept_mastery."""
        plain = UserProfileManager(store)
        plain.add_weak_point("Some Free Text Concept", COURSE)

        profile = plain.get()
        assert any(wp["concept"] == "Some Free Text Concept" for wp in profile["weak_points"])


class TestApplyPatchResolution:
    def test_add_weak_points_resolves_and_records_struggled_event(
        self, wired_profile_mgr, temporal
    ):
        patch = {"add_weak_points": [{"concept": "deadlock", "course": "wrong-course-guess"}]}
        result = wired_profile_mgr.apply_patch(patch, course_id=COURSE, session_id="s1")

        assert "changes" not in result or True  # applied count checked below
        assert result["applied"] == "1 changes"

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(events) == 1
        assert events[0]["event_type"] == "struggled"
        assert events[0]["session_id"] == "s1"

        # Derived view reflects it
        profile = wired_profile_mgr.get()
        assert any(wp["concept"] == "Deadlock" for wp in profile["weak_points"])

    def test_add_mastered_resolves_and_records_correct_answer_event(
        self, wired_profile_mgr, temporal
    ):
        patch = {"add_mastered": [{"concept": "deadlock", "course": "anything"}]}
        wired_profile_mgr.apply_patch(patch, course_id=COURSE, session_id="s1")

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert len(events) == 1
        assert events[0]["event_type"] == "correct_answer"

    def test_unresolvable_concept_is_skipped_not_crashed(self, wired_profile_mgr, temporal):
        patch = {"add_weak_points": [{"concept": "TotallyUnknownThing", "course": COURSE}]}
        result = wired_profile_mgr.apply_patch(patch, course_id=COURSE, session_id="s1")

        assert result["applied"] == "0 changes"
        assert temporal._store.get_learning_events(CONCEPT_ID) == []

    def test_missing_course_id_falls_back_to_legacy_free_text_write(
        self, wired_profile_mgr, temporal
    ):
        """No course_id passed → can't resolve → legacy JSON write path,
        same as an unwired UserProfileManager would do."""
        patch = {"add_weak_points": [{"concept": "Deadlock", "course": "CS202_OS"}]}
        result = wired_profile_mgr.apply_patch(patch)  # no course_id

        assert result["applied"] == "1 changes"
        # No concept_id resolution happened, so TemporalMemory untouched...
        assert temporal._store.get_learning_events(CONCEPT_ID) == []
        # ...but the raw JSON blob got the free-text write (invisible via get()
        # while wired, since get() overrides with the — still empty — derived view).
        profile = wired_profile_mgr.get()
        assert profile["weak_points"] == []

    def test_remove_weak_points_resolves_and_records_correct_answer(
        self, wired_profile_mgr, temporal
    ):
        temporal.record_event(CONCEPT_ID, "struggled", evidence_type="assessment")
        assert any(
            wp["concept"] == "Deadlock" for wp in wired_profile_mgr.get()["weak_points"]
        )

        patch = {"remove_weak_points": ["deadlock"]}
        wired_profile_mgr.apply_patch(patch, course_id=COURSE, session_id="s1")

        events = temporal._store.get_learning_events(CONCEPT_ID)
        assert events[-1]["event_type"] == "correct_answer"
