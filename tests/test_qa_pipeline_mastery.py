"""Tests for evidence-based mastery updates in QAPipeline."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from nexus.knowledge.qa_pipeline import MasteryEvidence, QAPipeline
from nexus.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = SQLiteStore(db_path)
    concepts = [
        ("cs202_os__deadlock", "Deadlock"),
        ("cs202_os__mutex", "Mutex"),
        ("cs202_os__semaphore", "Semaphore"),
        ("cs202_os__starvation", "Starvation"),
    ]
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO chunks (chunk_id, document_id, course_id, topic, type)
               VALUES (?, ?, ?, ?, ?)""",
            ("CS202_OS_001", "CS202_OS/doc1", "CS202_OS", "Concurrency", "definition"),
        )
        conn.executemany(
            "INSERT INTO concepts (concept_id, name, course_id) VALUES (?, ?, ?)",
            [(concept_id, name, "CS202_OS") for concept_id, name in concepts],
        )
        conn.executemany(
            "INSERT INTO chunk_concepts (chunk_id, concept_id) VALUES (?, ?)",
            [("CS202_OS_001", concept_id) for concept_id, _ in concepts],
        )

    yield db_path, store
    db_path.unlink(missing_ok=True)


def _make_pipeline(db_path: Path) -> QAPipeline:
    return QAPipeline(chroma_dir=Path(tempfile.mkdtemp()), sqlite_path=db_path)


def test_plain_qa_is_not_mastery_evidence(tmp_store):
    db_path, store = tmp_store
    pipeline = _make_pipeline(db_path)

    # Legacy callers may still invoke this hook without the new evidence arg.
    pipeline._update_concept_mastery(
        chunk_ids=["CS202_OS_001"],
        session_id="s1",
        user_query="什么是死锁？",
        response_summary="死锁是……",
    )

    assert store.get_mastery("cs202_os__deadlock") is None
    assert store.get_learning_events("cs202_os__deadlock") == []


def test_explicit_assessment_records_only_named_concept_with_context(tmp_store):
    db_path, store = tmp_store
    pipeline = _make_pipeline(db_path)

    pipeline._update_concept_mastery(
        chunk_ids=["CS202_OS_001"],
        session_id="quiz-1",
        user_query="Explain deadlock",
        response_summary="Student answer omitted",
        evidence=[
            MasteryEvidence(
                concept_id="cs202_os__deadlock",
                event_type="correct_answer",
                confidence=0.9,
                detail="Passed rubric items 1-3",
            )
        ],
    )

    row = store.get_mastery("cs202_os__deadlock")
    assert row["initial_mastery"] == pytest.approx(0.15)
    assert store.get_mastery("cs202_os__mutex") is None
    events = store.get_learning_events("cs202_os__deadlock")
    context = json.loads(events[0]["context"])
    assert context == {
        "evidence_type": "assessment",
        "confidence": 0.9,
        "detail": "Passed rubric items 1-3",
    }


@pytest.mark.parametrize(
    "evidence",
    [
        MasteryEvidence("cs202_os__deadlock", "reviewed"),
        MasteryEvidence("cs202_os__deadlock", "correct_answer", confidence=0.59),
        MasteryEvidence("cs202_os__deadlock", "correct_answer", source="llm_inference"),
        MasteryEvidence("outside__concept", "correct_answer"),
    ],
)
def test_invalid_or_unrelated_evidence_is_ignored(tmp_store, evidence):
    db_path, store = tmp_store
    pipeline = _make_pipeline(db_path)

    pipeline._update_concept_mastery(
        ["CS202_OS_001"], "s1", "question", "answer", evidence=[evidence]
    )

    assert store.get_mastery("cs202_os__deadlock") is None


def test_assessment_caps_updates_at_three_concepts(tmp_store):
    db_path, store = tmp_store
    pipeline = _make_pipeline(db_path)
    concept_ids = [
        "cs202_os__deadlock",
        "cs202_os__mutex",
        "cs202_os__semaphore",
        "cs202_os__starvation",
    ]

    pipeline._update_concept_mastery(
        ["CS202_OS_001"],
        "s1",
        "question",
        "answer",
        evidence=[MasteryEvidence(cid, "struggled") for cid in concept_ids],
    )

    assert sum(store.get_mastery(cid) is not None for cid in concept_ids) == 3
    assert store.get_mastery("cs202_os__starvation") is None


def test_record_assessment_is_explicit_public_entrypoint(tmp_store):
    db_path, store = tmp_store
    pipeline = _make_pipeline(db_path)

    assert pipeline.record_assessment(
        "cs202_os__deadlock",
        "forgot",
        session_id="self-report-1",
        source="user_self_report",
        detail="I learned this last week but cannot recall it",
    )
    assert not pipeline.record_assessment("cs202_os__mutex", "reviewed")

    events = store.get_learning_events("cs202_os__deadlock")
    assert events[0]["event_type"] == "forgot"
    assert json.loads(events[0]["context"])["evidence_type"] == "user_self_report"
