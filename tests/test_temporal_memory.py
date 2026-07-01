"""
Tests for Phase 4B: TemporalMemory (Ebbinghaus 遗忘曲线).

验证:
- 学习事件记录与 mastery 参数更新
- 遗忘曲线计算
- decaying concepts 检测
"""
from pathlib import Path
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.temporal_memory import TemporalMemory, MasterySnapshot


def _setup(tmp_path: Path) -> tuple[SQLiteStore, TemporalMemory]:
    store = SQLiteStore(tmp_path / "test.db")
    store.upsert_concept("os__deadlock", "Deadlock", "OS")
    store.upsert_concept("os__mutex", "Mutex", "OS")
    return store, TemporalMemory(store)


def test_record_event_correct_answer(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("os__deadlock", "correct_answer", session_id="s1")

    assert snap is not None
    assert snap.concept_id == "os__deadlock"
    assert snap.current_mastery == 0.15  # 0.0 + 0.15
    assert snap.review_count == 1
    assert snap.streak == 1


def test_record_event_struggled(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("os__deadlock", "struggled", session_id="s1")

    assert snap is not None
    assert snap.current_mastery == 0.05  # 0.0 + 0.05
    assert snap.decay_rate > 0.3  # decay_rate increased


def test_record_event_forgot_resets_streak(tmp_path):
    store, mem = _setup(tmp_path)

    # Build up a streak
    mem.record_event("os__deadlock", "correct_answer")
    mem.record_event("os__deadlock", "correct_answer")
    snap = mem.record_event("os__deadlock", "forgot")

    assert snap.streak == 0


def test_multiple_events_accumulate(tmp_path):
    store, mem = _setup(tmp_path)

    mem.record_event("os__deadlock", "correct_answer")
    mem.record_event("os__deadlock", "correct_answer")
    snap = mem.record_event("os__deadlock", "correct_answer")

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

    mem.record_event("os__deadlock", "correct_answer")
    mastery = mem.get_mastery("os__deadlock")

    # Just reviewed → mastery ≈ initial_mastery (very little decay)
    assert mastery > 0.1


def test_get_all_mastery(tmp_path):
    store, mem = _setup(tmp_path)

    mem.record_event("os__deadlock", "correct_answer")
    mem.record_event("os__mutex", "struggled")

    snapshots = mem.get_all_mastery()
    assert len(snapshots) == 2

    by_id = {s.concept_id: s for s in snapshots}
    assert by_id["os__deadlock"].current_mastery > by_id["os__mutex"].current_mastery


def test_get_all_mastery_filter_by_course(tmp_path):
    store, mem = _setup(tmp_path)
    store.upsert_concept("db__txn", "Transaction", "DB")

    mem.record_event("os__deadlock", "correct_answer")
    mem.record_event("db__txn", "correct_answer")

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

    snap = mem.record_event("nonexistent", "correct_answer")
    assert snap is None


def test_record_event_unknown_type(tmp_path):
    store, mem = _setup(tmp_path)

    snap = mem.record_event("os__deadlock", "invalid_type")
    assert snap is None
