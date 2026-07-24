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
