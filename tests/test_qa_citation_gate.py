"""Live QAPipeline citation gate tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.knowledge.llm_client import LLMOutput
from nexus.knowledge.qa_pipeline import QAPipeline
from nexus.knowledge.query_engine import RetrievedChunk, RetrievalResult


def _pipeline(answer: str, chunks: list[RetrievedChunk]) -> QAPipeline:
    pipeline = QAPipeline.__new__(QAPipeline)
    pipeline._sqlite_path = Path("unused.db")
    pipeline._session_mgr = SimpleNamespace(
        start_session=lambda *_: None,
        record_activity=lambda *_: None,
    )
    pipeline._memory_api = SimpleNamespace(
        get_profile=lambda: {"courses": []},
        get_mastery=lambda _: 0.0,
    )
    pipeline._query_engine = SimpleNamespace(
        retrieve=lambda query, **_: RetrievalResult(chunks, [query], "CS202"),
    )
    pipeline._graph_retriever = SimpleNamespace(retrieve=lambda **_: None)
    pipeline._sqlite = SimpleNamespace(get_concepts_for_chunks=lambda _: [])
    pipeline._llm = SimpleNamespace(
        chat=lambda _: LLMOutput(answer, "test-model", 10, 5),
        log_interaction=lambda **_: None,
    )
    return pipeline


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        content="A deadlock requires circular wait.",
        metadata={"source_file": "lecture.pdf", "topic": "Deadlock"},
        score=0.1,
        retrieval_path="both",
    )


@pytest.mark.parametrize("answer, marker", [
    ("Deadlock needs circular wait [S9].", "未知引用 [S9]"),
    ("Deadlock needs circular wait.", "缺少课件引用"),
])
def test_retrieval_backed_invalid_citations_are_rejected(answer: str, marker: str) -> None:
    response = _pipeline(answer, [_chunk()]).ask("What is deadlock?", course_id="CS202")

    assert response.model_used == "citation-gated"
    assert "[S1] Deadlock" in response.answer
    assert marker in response.warnings[0]


def test_no_retrieval_answer_is_not_citation_gated() -> None:
    response = _pipeline("General explanation without source ID.", []).ask(
        "What is quantum networking?", course_id="CS202"
    )

    assert response.answer == "General explanation without source ID."
    assert response.model_used == "test-model"
    assert all("引用校验" not in warning for warning in response.warnings)


def test_supported_paraphrase_keeps_generated_answer() -> None:
    answer = "Circular wait is required for a deadlock [S1]."
    response = _pipeline(answer, [_chunk()]).ask("What is deadlock?", course_id="CS202")

    assert response.answer == answer
    assert response.model_used == "test-model"
    assert all("结论未被课件支持" not in warning for warning in response.warnings)


def test_unsupported_conclusion_is_replaced_with_source_excerpt() -> None:
    answer = "A deadlock is caused by insufficient RAM [S1]."
    response = _pipeline(answer, [_chunk()]).ask("What is deadlock?", course_id="CS202")

    assert response.model_used == "citation-gated"
    assert "[S1] Deadlock" in response.answer
    assert "结论未被课件支持" in response.warnings[0]
    assert "insufficient RAM" not in response.answer


def test_negated_conclusion_is_not_entailed_by_positive_source() -> None:
    answer = "A deadlock does not require circular wait [S1]."
    response = _pipeline(answer, [_chunk()]).ask("What is deadlock?", course_id="CS202")

    assert response.model_used == "citation-gated"
    assert "结论未被课件支持" in response.warnings[0]


def test_mixed_sentences_fail_if_any_cited_claim_is_unsupported() -> None:
    answer = (
        "Circular wait is required for a deadlock [S1]. "
        "It is caused by insufficient RAM [S1]."
    )
    response = _pipeline(answer, [_chunk()]).ask("What is deadlock?", course_id="CS202")

    assert response.model_used == "citation-gated"
    assert "结论未被课件支持" in response.warnings[0]
    assert "insufficient RAM" not in response.answer


def test_trailing_citation_after_period_is_bound_to_previous_sentence() -> None:
    good = "Circular wait is required for a deadlock. [S1]"
    kept = _pipeline(good, [_chunk()]).ask("What is deadlock?", course_id="CS202")
    assert kept.answer == good
    assert kept.model_used == "test-model"

    for bad in (
        "A deadlock is caused by insufficient RAM. [S1]",
        "A deadlock is caused by insufficient RAM. [S1].",
    ):
        gated = _pipeline(bad, [_chunk()]).ask("What is deadlock?", course_id="CS202")
        assert gated.model_used == "citation-gated"
        assert "结论未被课件支持" in gated.warnings[0]


def test_positive_claim_is_not_entailed_by_negated_source_sentence() -> None:
    chunk = RetrievedChunk(
        chunk_id="c1",
        content="Deadlock is not starvation.",
        metadata={"source_file": "lecture.pdf", "topic": "Deadlock"},
        score=0.1,
        retrieval_path="both",
    )
    response = _pipeline("Deadlock is starvation [S1].", [chunk]).ask(
        "What is deadlock?", course_id="CS202"
    )
    assert response.model_used == "citation-gated"
    assert "结论未被课件支持" in response.warnings[0]


def test_unrelated_source_negation_does_not_veto_positive_paraphrase() -> None:
    chunk = RetrievedChunk(
        chunk_id="c1",
        content="A deadlock requires circular wait. Deadlock is not starvation.",
        metadata={"source_file": "lecture.pdf", "topic": "Deadlock"},
        score=0.1,
        retrieval_path="both",
    )
    answer = "Circular wait is required for a deadlock [S1]."
    response = _pipeline(answer, [chunk]).ask("What is deadlock?", course_id="CS202")

    assert response.answer == answer
    assert response.model_used == "test-model"

    negated = _pipeline(
        "A deadlock does not require circular wait [S1].", [chunk]
    ).ask("What is deadlock?", course_id="CS202")
    assert negated.model_used == "citation-gated"
    assert "结论未被课件支持" in negated.warnings[0]


def test_assessment_question_is_not_entailment_gated() -> None:
    question = "What are the four necessary conditions for deadlock [S1]?"
    response = _pipeline(question, [_chunk()]).ask(
        "Quiz me on deadlock", course_id="CS202", teaching_mode="assessment"
    )

    assert response.answer == question
    assert response.model_used == "test-model"
    assert all("结论未被课件支持" not in warning for warning in response.warnings)
