"""
Tests for SessionManager persistence (Phase 4B follow-up).

Session state used to live only in an in-memory dict (self._sessions),
so a process restart lost all active sessions and their pending idle/
end_session-triggered profile updates. It's now persisted to the
active_sessions SQLite table.

These tests specifically simulate "restart" by constructing a brand new
SessionManager against the same SQLiteStore/db file, rather than reusing
the original instance.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nexus.knowledge.session_manager import SessionManager
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.user_profile import UserProfileManager

SESSION = "sess-1"
COURSE = "CS202_OS"


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "nexus.db")


def _make_manager(store: SQLiteStore, idle_timeout_minutes: int = 5) -> SessionManager:
    """Fresh SessionManager instance — simulates a process restart when
    called again against the same store."""
    return SessionManager(
        sqlite_store=store,
        profile_manager=UserProfileManager(store),
        idle_timeout_minutes=idle_timeout_minutes,
    )


class TestPersistenceAcrossRestart:
    def test_start_session_survives_new_instance(self, store):
        mgr1 = _make_manager(store)
        mgr1.start_session(SESSION, course_id=COURSE)

        # Simulate restart: brand new SessionManager, same store
        mgr2 = _make_manager(store)
        assert mgr2.is_idle(SESSION) is False

    def test_record_activity_survives_new_instance(self, store):
        mgr1 = _make_manager(store)
        mgr1.start_session(SESSION)
        mgr1.record_activity(SESSION, course_id=COURSE)
        mgr1.record_activity(SESSION)

        mgr2 = _make_manager(store)
        state = mgr2._load_state(SESSION)
        assert state is not None
        assert state.course_id == COURSE
        assert state.interaction_count == 2

    def test_course_id_set_only_once(self, store):
        """course_id should stick to the first non-None value seen,
        matching the pre-persistence behavior."""
        mgr = _make_manager(store)
        mgr.start_session(SESSION)
        mgr.record_activity(SESSION, course_id="FIRST")
        mgr.record_activity(SESSION, course_id="SECOND")

        state = mgr._load_state(SESSION)
        assert state.course_id == "FIRST"

    def test_unregistered_session_is_idle(self, store):
        mgr = _make_manager(store)
        assert mgr.is_idle("never-started") is True


class TestIdleDetectionAcrossRestart:
    def test_idle_timeout_detected_after_restart(self, store):
        mgr1 = _make_manager(store, idle_timeout_minutes=5)
        mgr1.start_session(SESSION)

        # Force last_active into the past directly via the store,
        # simulating time passing across a restart.
        row = store.get_session(SESSION)
        stale_time = (datetime.now() - timedelta(minutes=10)).isoformat()
        store.upsert_session(
            session_id=SESSION,
            started_at=row["started_at"],
            last_active=stale_time,
            course_id=row["course_id"],
            interaction_count=row["interaction_count"],
            ended=False,
        )

        mgr2 = _make_manager(store, idle_timeout_minutes=5)
        assert mgr2.is_idle(SESSION) is True

    def test_check_and_end_idle_sessions_finds_persisted_session(self, store):
        mgr1 = _make_manager(store, idle_timeout_minutes=5)
        mgr1.start_session(SESSION)
        row = store.get_session(SESSION)
        stale_time = (datetime.now() - timedelta(minutes=10)).isoformat()
        store.upsert_session(
            session_id=SESSION,
            started_at=row["started_at"],
            last_active=stale_time,
            course_id=row["course_id"],
            interaction_count=row["interaction_count"],
            ended=False,
        )

        # Fresh instance (simulating restart) still finds and ends it.
        mgr2 = _make_manager(store, idle_timeout_minutes=5)
        ended = mgr2.check_and_end_idle_sessions()
        assert ended == [SESSION]
        assert store.get_session(SESSION) is None


class TestEndSessionCleansUpRow:
    def test_end_session_deletes_row_too_few_interactions(self, store):
        mgr = _make_manager(store)
        mgr.start_session(SESSION)

        result = mgr.end_session(SESSION)
        assert result["status"] == "skipped"
        assert store.get_session(SESSION) is None

    def test_end_session_deletes_row_after_full_analysis(self, store):
        mgr = _make_manager(store)
        mgr.start_session(SESSION, course_id=COURSE)
        store.log_interaction(SESSION, "q1", "a1", course_id=COURSE)
        store.log_interaction(SESSION, "q2", "a2", course_id=COURSE)

        # No LLM API key configured in test env -> patch analysis returns None,
        # profile update is skipped, but the session row must still be cleaned up.
        result = mgr.end_session(SESSION)
        assert result["status"] == "completed"
        assert store.get_session(SESSION) is None

    def test_end_session_not_found(self, store):
        mgr = _make_manager(store)
        result = mgr.end_session("nonexistent")
        assert result["status"] == "not_found"

    def test_end_session_already_ended_guard(self, store):
        mgr = _make_manager(store)
        mgr.start_session(SESSION)
        # Manually mark as ended without deleting (simulates a reentrant call
        # racing the delete at the end of end_session).
        row = store.get_session(SESSION)
        store.upsert_session(
            session_id=SESSION,
            started_at=row["started_at"],
            last_active=row["last_active"],
            course_id=row["course_id"],
            interaction_count=row["interaction_count"],
            ended=True,
        )
        result = mgr.end_session(SESSION)
        assert result["status"] == "already_ended"
