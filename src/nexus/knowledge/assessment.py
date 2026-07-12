"""Explicit assessment state used by the QA pipeline and UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AssessmentTarget:
    concept_id: str
    concept_name: str
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssessmentGrade:
    event_type: str
    confidence: float
    feedback: str


@dataclass
class AssessmentAttempt:
    session_id: str
    course_id: str | None
    question: str
    target: AssessmentTarget
    status: str = "awaiting_answer"
    student_answer: str | None = None
    grade: AssessmentGrade | None = None
    recorded: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def complete(self, student_answer: str, grade: AssessmentGrade, *, recorded: bool) -> None:
        if self.status != "awaiting_answer":
            raise ValueError("Assessment is not waiting for an answer")
        answer = student_answer.strip()
        if not answer:
            raise ValueError("Student answer must not be empty")
        self.student_answer = answer
        self.grade = grade
        self.recorded = recorded
        self.status = "completed"
