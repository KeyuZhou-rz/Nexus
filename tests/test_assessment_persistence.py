from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from nexus.knowledge.assessment import AssessmentAttempt, AssessmentTarget
from nexus.knowledge.qa_pipeline import QAPipeline, _assessment_from_row
from nexus.knowledge.sqlite_store import SQLiteStore


def _seed(store: SQLiteStore) -> None:
    store.upsert_concept("cs__concept", "Concept", "CS")


def _metadata() -> dict:
    return {
        "rubric": ["Use the course definition"],
        "reference_answer": "Course answer",
        "source_evidence": [
            {
                "source_id": "S1",
                "source_file": "lecture.pdf",
                "chunk_id": "chunk-1",
                "content": "Course evidence",
            }
        ],
    }


def _persist(store: SQLiteStore, attempt_id: str = "attempt-1") -> None:
    store.create_assessment_attempt(
        attempt_id=attempt_id,
        session_id="session-1",
        course_id="CS",
        concept_id="cs__concept",
        concept_name="Concept",
        question="Question?",
        source_refs=["S1:lecture.pdf#chunk-1"],
        metadata=_metadata(),
    )


def test_pending_attempt_survives_store_and_pipeline_restart(tmp_path) -> None:
    db_path = tmp_path / "nexus.db"
    first = SQLiteStore(db_path)
    _seed(first)
    _persist(first)

    restarted = SQLiteStore(db_path)
    pipeline = QAPipeline.__new__(QAPipeline)
    pipeline._sqlite = restarted
    attempt = pipeline.get_pending_assessment("session-1")

    assert attempt is not None
    assert attempt.attempt_id == "attempt-1"
    assert attempt.status == "awaiting_answer"
    assert attempt.metadata["rubric"] == ["Use the course definition"]
    assert attempt.target.source_refs == ("S1:lecture.pdf#chunk-1",)


def test_claim_is_atomic_across_connections_and_completed_attempt_is_auditable(tmp_path) -> None:
    db_path = tmp_path / "nexus.db"
    first = SQLiteStore(db_path)
    _seed(first)
    _persist(first)
    second = SQLiteStore(db_path)

    barrier = threading.Barrier(2)
    claims: list[bool] = []

    def claim(store: SQLiteStore, answer: str) -> None:
        barrier.wait()
        claims.append(store.claim_assessment_attempt("attempt-1", answer))

    threads = [
        threading.Thread(target=claim, args=(first, "answer-a")),
        threading.Thread(target=claim, args=(second, "answer-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(claims) == [False, True]
    assert first.complete_assessment_attempt(
        "attempt-1",
        event_type="correct_answer",
        confidence=0.9,
        feedback="Correct",
        recorded=True,
    )
    row = second.get_assessment_attempt("attempt-1")
    assert row["status"] == "completed"
    assert row["recorded"] == 1
    assert second.get_pending_assessment("session-1") is None
    assert not second.claim_assessment_attempt("attempt-1", "duplicate")


def test_concurrent_pipeline_submit_records_mastery_once(tmp_path) -> None:
    db_path = tmp_path / "nexus.db"
    store = SQLiteStore(db_path)
    _seed(store)
    _persist(store)
    row = store.get_assessment_attempt("attempt-1")
    attempts = [_assessment_from_row(row), _assessment_from_row(row)]
    recorded: list[str] = []
    lock = threading.Lock()

    def pipeline() -> QAPipeline:
        instance = QAPipeline.__new__(QAPipeline)
        instance._sqlite = SQLiteStore(db_path)

        def grade(messages):
            time.sleep(0.05)
            return SimpleNamespace(
                content=json.dumps(
                    {"event_type": "correct_answer", "confidence": 0.9, "feedback": "Correct"}
                )
            )

        instance._llm = SimpleNamespace(chat=grade)

        def record(*args, **kwargs):
            with lock:
                recorded.append(args[0])
            return True

        instance.record_assessment = record
        return instance

    outcomes: list[str] = []

    def submit(attempt: AssessmentAttempt) -> None:
        try:
            pipeline().submit_assessment(attempt, "Student answer")
            outcomes.append("completed")
        except ValueError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=submit, args=(attempt,)) for attempt in attempts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["completed", "rejected"]
    assert recorded == ["cs__concept"]
    assert store.get_assessment_attempt("attempt-1")["status"] == "completed"
