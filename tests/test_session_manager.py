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
from nexus.cli.sessions import main as sessions_main

SESSION = "sess-1"
COURSE = "CS202_OS"


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(tmp_path / "nexus.db")


@pytest.fixture(autouse=True)
def disable_live_llm(monkeypatch):
    """Session persistence tests must not depend on a developer's local LLM config."""
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")


def _make_manager(
    store: SQLiteStore,
    idle_timeout_minutes: int = 5,
    retry_delay_seconds: int = 60,
) -> SessionManager:
    """Fresh SessionManager instance — simulates a process restart when
    called again against the same store."""
    return SessionManager(
        sqlite_store=store,
        profile_manager=UserProfileManager(store),
        idle_timeout_minutes=idle_timeout_minutes,
        retry_delay_seconds=retry_delay_seconds,
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

    def test_finalize_is_idempotent_across_two_managers(self, store):
        mgr1 = _make_manager(store)
        mgr2 = _make_manager(store)
        mgr1.start_session(SESSION)
        store.log_interaction(SESSION, "q1", "a1", course_id=COURSE)
        store.log_interaction(SESSION, "q2", "a2", course_id=COURSE)

        first = mgr1.end_session(SESSION)
        second = mgr2.end_session(SESSION)
        assert first["status"] == "completed"
        assert second["status"] == "not_found"
        assert len(store.get_session_logs(SESSION)) == 2

    def test_cli_finalize_idle_is_scheduler_callable(self, store, capsys):
        mgr = _make_manager(store, idle_timeout_minutes=5)
        mgr.start_session(SESSION)
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
        assert sessions_main(["--db-path", str(store.db_path), "finalize-idle", "--json"]) == 0
        assert '"count": 1' in capsys.readouterr().out
        assert store.get_session(SESSION) is None


class TestRetryableFinalization:
    def test_first_timeout_then_second_attempt_succeeds_once(self, store, monkeypatch):
        mgr = _make_manager(store, retry_delay_seconds=0)
        mgr.start_session(SESSION, course_id=COURSE)
        store.log_interaction(SESSION, "q1", "a1", course_id=COURSE)
        store.log_interaction(SESSION, "q2", "a2", course_id=COURSE)
        attempts = iter([TimeoutError("gateway timeout"), {"notes": "ok"}])

        def analyze(_transcript):
            value = next(attempts)
            if isinstance(value, Exception):
                raise value
            return value

        applied = []
        monkeypatch.setattr(mgr, "_analyze_session", analyze)
        monkeypatch.setattr(
            mgr._profile,
            "apply_patch",
            lambda patch, **kwargs: applied.append(patch) or {"applied": "1 changes"},
        )

        first = mgr.end_session(SESSION)
        row = store.get_session(SESSION)
        assert first["status"] == "retry"
        assert row["ended"] == 0
        assert row["finalize_status"] == "retry"
        assert row["finalize_attempts"] == 1
        assert "gateway timeout" in row["finalize_last_error"]

        second = mgr.end_session(SESSION)
        assert second["status"] == "completed"
        assert applied == [{"notes": "ok"}]
        assert store.get_session(SESSION) is None

    def test_concurrent_claim_allows_only_one_finalizer(self, store):
        mgr1 = _make_manager(store)
        mgr2 = _make_manager(store)
        mgr1.start_session(SESSION)
        assert store.claim_session_end(SESSION) is True
        assert store.claim_session_end(SESSION) is False
        assert mgr2.end_session(SESSION)["status"] == "already_ended"

    def test_disabled_llm_finishes_without_retry(self, store, monkeypatch):
        from nexus.knowledge.session_manager import gateway

        mgr = _make_manager(store)
        mgr.start_session(SESSION)
        store.log_interaction(SESSION, "q1", "a1")
        store.log_interaction(SESSION, "q2", "a2")
        mgr._api_key = "configured-but-disabled"
        monkeypatch.setattr(gateway, "_enabled", False)
        monkeypatch.setattr(
            mgr,
            "_call_llm_litellm",
            lambda prompt: (_ for _ in ()).throw(AssertionError("must not call LLM")),
        )
        result = mgr.end_session(SESSION)
        assert result["status"] == "completed"
        assert store.get_session(SESSION) is None
        assert len(store.get_session_logs(SESSION)) == 2

    def test_abandon_is_explicit_and_retains_logs(self, store):
        mgr = _make_manager(store)
        mgr.start_session(SESSION)
        store.log_interaction(SESSION, "q1", "a1")
        result = mgr.abandon_session(SESSION, "operator decision")
        assert result["status"] == "abandoned"
        row = store.get_session(SESSION)
        assert row["finalize_status"] == "abandoned"
        assert row["ended"] == 1
        assert len(store.get_session_logs(SESSION)) == 1


class TestUpsertSessionPreservesFinalizeColumns:
    def test_heartbeat_does_not_reset_finalize_retry_state(self, store):
        store.upsert_session(
            session_id=SESSION,
            started_at="2026-07-19T10:00:00",
            last_active="2026-07-19T10:00:00",
            course_id=COURSE,
            interaction_count=2,
            ended=False,
        )
        assert store.claim_session_end(SESSION) is True

        next_retry = "2026-07-19T11:00:00"
        store.release_session_retry(SESSION, error="transient failure", next_retry=next_retry)
        row = store.get_session(SESSION)
        assert row["finalize_status"] == "retry"
        assert row["finalize_attempts"] == 1
        assert row["finalize_last_error"] == "transient failure"
        assert row["finalize_next_retry"] == next_retry

        store.upsert_session(
            session_id=SESSION,
            started_at=row["started_at"],
            last_active="2026-07-19T10:05:00",
            course_id=COURSE,
            interaction_count=3,
            ended=False,
        )
        row = store.get_session(SESSION)
        assert row["finalize_status"] == "retry"
        assert row["finalize_attempts"] == 1
        assert row["finalize_last_error"] == "transient failure"
        assert row["finalize_next_retry"] == next_retry
