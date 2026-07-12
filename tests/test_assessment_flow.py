from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nexus.knowledge.assessment import AssessmentAttempt, AssessmentGrade, AssessmentTarget
from nexus.knowledge.qa_pipeline import (
    ChunkSource,
    QAPipeline,
    QAResponse,
    _parse_assessment_grade,
    _select_assessment_target,
)


def _response() -> QAResponse:
    return QAResponse(
        answer="Define deadlock and name its necessary conditions.",
        sources=[ChunkSource("chunk-1", "Deadlock", "lecture.pdf", 3, 10, 0.9)],
        session_id="quiz-1",
        course_id="CS202_OS",
        course_inferred=False,
        model_used="test-model",
        expanded_queries=[],
        estimated_input_tokens=20,
    )


def test_create_assessment_waits_for_answer_and_does_not_record_mastery() -> None:
    pipeline = QAPipeline.__new__(QAPipeline)
    calls: list[dict] = []
    pipeline.ask = lambda *args, **kwargs: (calls.append(kwargs) or _response())
    pipeline._sqlite = SimpleNamespace(
        get_concepts_for_chunks=lambda ids: [
            {"concept_id": "cs202__deadlock", "name": "Deadlock"}
        ]
    )
    pipeline.record_assessment = lambda *args, **kwargs: pytest.fail("must not record before submission")

    attempt = pipeline.create_assessment("Deadlock", session_id="quiz-1", course_id="CS202_OS")

    assert attempt.status == "awaiting_answer"
    assert attempt.student_answer is None
    assert attempt.target.concept_id == "cs202__deadlock"
    assert attempt.target.source_refs == ("S1:lecture.pdf#chunk-1",)
    assert calls[0]["teaching_mode"] == "assessment"


def test_submit_assessment_grades_then_records_auditable_evidence() -> None:
    pipeline = QAPipeline.__new__(QAPipeline)
    pipeline._llm = SimpleNamespace(
        chat=lambda messages: SimpleNamespace(
            content=json.dumps(
                {"event_type": "correct_answer", "confidence": 0.88, "feedback": "Core conditions are correct."}
            )
        )
    )
    recorded: list[tuple[tuple, dict]] = []
    pipeline.record_assessment = lambda *args, **kwargs: (recorded.append((args, kwargs)) or True)
    attempt = AssessmentAttempt(
        session_id="quiz-1",
        course_id="CS202_OS",
        question="What is deadlock?",
        target=AssessmentTarget("cs202__deadlock", "Deadlock", ("S1:lecture.pdf#chunk-1",)),
    )

    result = pipeline.submit_assessment(attempt, "A circular wait where processes cannot proceed.")

    assert result.status == "completed"
    assert result.recorded is True
    args, kwargs = recorded[0]
    assert args == ("cs202__deadlock", "correct_answer")
    assert kwargs["source"] == "assessment"
    assert kwargs["confidence"] == pytest.approx(0.88)
    detail = json.loads(kwargs["detail"])
    assert detail["concept"] == "Deadlock"
    assert detail["source_refs"] == ["S1:lecture.pdf#chunk-1"]
    assert detail["student_answer"].startswith("A circular wait")


def test_assessment_cannot_be_submitted_twice() -> None:
    attempt = AssessmentAttempt(
        "quiz", "CS", "Question", AssessmentTarget("cid", "Concept")
    )
    attempt.complete("answer", AssessmentGrade("struggled", 0.8, "feedback"), recorded=True)
    pipeline = QAPipeline.__new__(QAPipeline)
    with pytest.raises(ValueError, match="already been submitted"):
        pipeline.submit_assessment(attempt, "another answer")


def test_target_selection_binds_to_the_question_concept() -> None:
    rows = [
        {"concept_id": "b", "name": "Mutex"},
        {"concept_id": "a", "name": "Deadlock"},
    ]
    assert _select_assessment_target("Quiz me on Mutex", rows)["concept_id"] == "b"
    assert (
        _select_assessment_target(
            "Concurrency quiz",
            rows,
            question="Explain how a Mutex provides mutual exclusion.",
        )["concept_id"]
        == "b"
    )


def test_target_selection_rejects_ambiguous_multi_concept_question() -> None:
    rows = [
        {"concept_id": "b", "name": "Mutex"},
        {"concept_id": "a", "name": "Deadlock"},
    ]
    assert _select_assessment_target("Concurrency quiz", rows) is None
    assert (
        _select_assessment_target(
            "Concurrency quiz",
            rows,
            question="Compare Mutex behavior with Deadlock prevention.",
        )
        is None
    )


def test_grader_accepts_json_markdown_fence() -> None:
    grade = _parse_assessment_grade(
        '```json\n{"event_type":"struggled","confidence":0.75,"feedback":"Review it."}\n```'
    )
    assert grade == AssessmentGrade("struggled", 0.75, "Review it.")
