from __future__ import annotations

from pathlib import Path

from nexus.knowledge.store import ChromaKnowledgeStore


def test_chroma_store_close_is_idempotent(tmp_path: Path) -> None:
    store = ChromaKnowledgeStore(tmp_path / "chroma", per_course=True)
    store.close()
    store.close()
    assert store._client is None


def test_chroma_store_context_manager_closes(tmp_path: Path) -> None:
    with ChromaKnowledgeStore(tmp_path / "chroma", per_course=True) as store:
        assert store._client is not None
    assert store._client is None
