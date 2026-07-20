from __future__ import annotations

from nexus.knowledge.query_engine import (
    QueryEngine,
    _expand_query_rule,
    _extract_keywords,
)
from nexus.knowledge.sqlite_store import SQLiteStore


def test_rule_expansion_extracts_keywords() -> None:
    expanded = _expand_query_rule("What is deadlock in operating systems?")
    assert "What is deadlock in operating systems?" in expanded
    keywords = _extract_keywords("What is deadlock in operating systems?")
    assert "deadlock" in keywords
    assert any("deadlock" in q.lower() for q in expanded[1:])


def test_rule_expansion_uses_concept_aliases(tmp_path_factory, monkeypatch) -> None:
    db_path = tmp_path_factory.mktemp("qe") / "nexus.db"
    store = SQLiteStore(db_path)
    store.upsert_concept(
        concept_id="cs202_os__deadlock",
        name="Deadlock",
        course_id="CS202_OS",
        definition="A situation where two processes block each other.",
    )
    store.upsert_alias(
        alias="deadlock",
        course_id="CS202_OS",
        concept_id="cs202_os__deadlock",
    )

    monkeypatch.setattr(
        "nexus.knowledge.sqlite_store.default_db_path", lambda: db_path
    )

    expanded = _expand_query_rule(
        "Explain deadlock prevention",
        course_id="CS202_OS",
    )
    assert any("Deadlock" in q for q in expanded)


def test_rule_expansion_does_not_call_llm(monkeypatch) -> None:
    called = False

    def fake_chat(*args, **kwargs):
        nonlocal called
        called = True
        raise RuntimeError("should not be called")

    monkeypatch.setattr("nexus.knowledge.query_engine.gateway.chat", fake_chat)
    expanded = _expand_query_rule("virtual memory paging")
    assert not called
    assert isinstance(expanded, list)
    assert len(expanded) >= 1


def test_query_engine_falls_back_to_rule_only_on_llm_failure(
    tmp_path_factory, monkeypatch
) -> None:
    chroma_dir = tmp_path_factory.mktemp("chroma")
    engine = QueryEngine(chroma_dir, use_llm_expansion=True)

    def failing_chat(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr("nexus.knowledge.query_engine.gateway.chat", failing_chat)
    monkeypatch.setattr(
        "nexus.knowledge.query_engine.gateway._enabled", True
    )
    monkeypatch.setattr(
        "nexus.knowledge.query_engine.gateway._api_key", "test-key"
    )

    expanded = engine.expand_query("page fault handling", course_id="CS202_OS")
    assert isinstance(expanded, list)
    assert len(expanded) >= 1
    assert any("page" in q.lower() for q in expanded)


def test_rule_expansion_is_deduplicated_and_capped() -> None:
    expanded = _expand_query_rule("deadlock deadlock deadlock")
    seen = set()
    for q in expanded:
        assert q.strip().lower() not in seen
        seen.add(q.strip().lower())
    assert len(expanded) <= 3
