"""
Tests for Phase 4B: TemporalMemory (Ebbinghaus 遗忘曲线).

验证:
- 学习事件记录与 mastery 参数更新
- 遗忘曲线计算
- decaying concepts 检测
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from nexus.knowledge.sqlite_store import SQLiteStore, connect
from nexus.knowledge.temporal_memory import TemporalMemory, MasterySnapshot


def _setup(tmp_path: Path) -> tuple[SQLiteStore, TemporalMemory]:
    store = SQLiteStore(tmp_path / "test.db")
    store.upsert_concept("os__deadlock", "Deadlock", "OS")
    store.upsert_concept("os__mutex", "Mutex", "OS")
    return store, TemporalMemory(store)


def test_record_event_correct_answer(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("os__deadlock", "correct_answer", session_id="s1", evidence_type="assessment")

    assert snap is not None
    assert snap.concept_id == "os__deadlock"
    assert snap.current_mastery == 0.15  # 0.0 + 0.15
    assert snap.review_count == 1
    assert snap.streak == 1


def test_record_event_struggled(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("os__deadlock", "struggled", session_id="s1", evidence_type="assessment")

    assert snap is not None
    assert snap.current_mastery == 0.05  # 0.0 + 0.05
    assert snap.decay_rate > 0.3  # decay_rate increased


def test_record_event_forgot_resets_streak(tmp_path):
    store, mem = _setup(tmp_path)

    # Build up a streak
    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    snap = mem.record_event("os__deadlock", "forgot", evidence_type="assessment")

    assert snap.streak == 0


def test_multiple_events_accumulate(tmp_path):
    store, mem = _setup(tmp_path)

    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    snap = mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")

    # 3 correct answers: mastery should be ~0.45 (3 * 0.15)
    assert snap.current_mastery > 0.4
    assert snap.review_count == 3
    assert snap.streak == 3


def test_get_mastery_for_unknown_concept(tmp_path):
    store, mem = _setup(tmp_path)

    # No events recorded yet → mastery = 0
    assert mem.get_mastery("os__deadlock") == 0.0


def test_get_mastery_after_event(tmp_path):
    store, mem = _setup(tmp_path)

    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    mastery = mem.get_mastery("os__deadlock")

    # Just reviewed → mastery ≈ initial_mastery (very little decay)
    assert mastery > 0.1


def test_get_all_mastery(tmp_path):
    store, mem = _setup(tmp_path)

    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    mem.record_event("os__mutex", "struggled", evidence_type="assessment")

    snapshots = mem.get_all_mastery()
    assert len(snapshots) == 2

    by_id = {s.concept_id: s for s in snapshots}
    assert by_id["os__deadlock"].current_mastery > by_id["os__mutex"].current_mastery


def test_get_all_mastery_exposes_initial_mastery(tmp_path):
    """initial_mastery must be exposed so the forgetting-curve chart can plot
    each concept's own un-decayed starting point instead of double-decaying
    an already-decayed current_mastery."""
    store, mem = _setup(tmp_path)

    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    snap = mem.get_all_mastery()[0]

    assert snap.initial_mastery == 0.15
    assert snap.current_mastery <= snap.initial_mastery


def test_get_all_mastery_filter_by_course(tmp_path):
    store, mem = _setup(tmp_path)
    store.upsert_concept("db__txn", "Transaction", "DB")

    mem.record_event("os__deadlock", "correct_answer", evidence_type="assessment")
    mem.record_event("db__txn", "correct_answer", evidence_type="assessment")

    os_snaps = mem.get_all_mastery(course_id="OS")
    assert len(os_snaps) == 1
    assert os_snaps[0].course_id == "OS"


def test_classify_urgency():
    assert MasterySnapshot.classify_urgency(0.9) == "mastered"
    assert MasterySnapshot.classify_urgency(0.6) == "stable"
    assert MasterySnapshot.classify_urgency(0.35) == "decaying"
    assert MasterySnapshot.classify_urgency(0.1) == "critical"


def test_record_event_nonexistent_concept(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("nonexistent", "correct_answer", evidence_type="assessment")
    assert snap is None


def test_record_event_unknown_type(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("os__deadlock", "invalid_type", evidence_type="assessment")
    assert snap is None


def test_concurrent_correct_answers_are_accumulated_atomically(tmp_path):
    db_path = tmp_path / "concurrent.db"
    setup_store = SQLiteStore(db_path)
    setup_store.upsert_concept("os__deadlock", "Deadlock", "OS")
    memories = [
        TemporalMemory(SQLiteStore(db_path)),
        TemporalMemory(SQLiteStore(db_path)),
    ]
    barrier = Barrier(2)

    def record(memory: TemporalMemory):
        barrier.wait(timeout=5)
        return memory.record_event("os__deadlock", "correct_answer", evidence_type="assessment")

    with ThreadPoolExecutor(max_workers=2) as executor:
        snapshots = list(executor.map(record, memories))

    assert sorted(snapshot.current_mastery for snapshot in snapshots) == pytest.approx(
        [0.15, 0.30]
    )
    mastery = setup_store.get_mastery("os__deadlock")
    assert mastery["initial_mastery"] == pytest.approx(0.30)
    assert mastery["decay_rate"] == pytest.approx(0.3 * 0.85 * 0.85)
    assert mastery["review_count"] == 2
    assert mastery["streak"] == 2
    events = setup_store.get_learning_events("os__deadlock", trusted_only=True)
    assert len(events) == 2
    assert {event["event_type"] for event in events} == {"correct_answer"}


def test_mastery_failure_rolls_back_learning_event(tmp_path):
    store, memory = _setup(tmp_path)

    with connect(store.db_path) as conn:
        conn.execute(
            """CREATE TRIGGER reject_mastery BEFORE INSERT ON concept_mastery
               BEGIN SELECT RAISE(ABORT, 'mastery rejected'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="mastery rejected"):
        memory.record_event("os__deadlock", "correct_answer", evidence_type="assessment")

    assert store.get_learning_events("os__deadlock") == []
    assert store.get_mastery("os__deadlock") is None
