from __future__ import annotations

from pathlib import Path

from nexus.knowledge import read as read_module
from nexus.knowledge.store import ChromaKnowledgeStore


class _StoreStub:
    last = None

    def __init__(self, *_args, **_kwargs):
        pass

    def fetch_chunks(self, *, where=None, limit=20, course_id=None):
        _StoreStub.last = {"where": where, "limit": limit, "course_id": course_id}
        return {
            "documents": ["second", "first"],
            "metadatas": [
                {"file_name": "a.md", "chunk_index": "2", "course_id": "EE201"},
                {"file_name": "a.md", "chunk_index": "1", "course_id": "EE201"},
            ],
        }

    def close(self):
        pass


def test_read_chunks_with_filters_and_sort(monkeypatch):
    monkeypatch.setattr(read_module, "ChromaKnowledgeStore", _StoreStub)

    summary = read_module.read_chunks(
        Path("data/chroma"),
        course_id="EE201",
        doc_type="notes",
        source_path="/tmp/a.md",
        limit=5,
    )

    assert len(summary.items) == 2
    assert summary.items[0].text == "first"
    assert _StoreStub.last["limit"] == 5
    assert "$and" in _StoreStub.last["where"]


import pytest


@pytest.fixture
def populated_read_store(tmp_path: Path):
    persist_dir = tmp_path / "chroma"
    with ChromaKnowledgeStore(persist_dir, per_course=True) as store:
        store.upsert_chunks(
            ids=["t1:1", "t1:2"],
            texts=["TEST101 first chunk", "TEST101 second chunk"],
            metadatas=[
                {"file_name": "t1.md", "chunk_index": 0, "course_id": "TEST101", "doc_type": "notes"},
                {"file_name": "t1.md", "chunk_index": 1, "course_id": "TEST101", "doc_type": "notes"},
            ],
            course_id="TEST101",
        )
        store.upsert_chunks(
            ids=["t2:1"],
            texts=["TEST102 only chunk"],
            metadatas=[
                {"file_name": "t2.md", "chunk_index": 0, "course_id": "TEST102", "doc_type": "notes"},
            ],
            course_id="TEST102",
        )

    legacy_store = ChromaKnowledgeStore(persist_dir, per_course=False, collection_name="nexus_chunks")
    try:
        legacy_store.upsert_chunks(
            ids=["legacy:1"],
            texts=["Legacy collection chunk"],
            metadatas=[
                {"file_name": "legacy.md", "chunk_index": 0, "course_id": "LEGACY", "doc_type": "notes"},
            ],
        )
    finally:
        legacy_store.close()

    return persist_dir


def test_read_chunks_per_course_filters_by_course_id(populated_read_store: Path) -> None:
    summary = read_module.read_chunks(populated_read_store, course_id="TEST101")
    texts = {item.text for item in summary.items}
    assert texts == {"TEST101 first chunk", "TEST101 second chunk"}
    assert all(item.metadata.get("course_id") == "TEST101" for item in summary.items)


def test_read_chunks_without_course_id_spans_collections(populated_read_store: Path) -> None:
    summary = read_module.read_chunks(populated_read_store)
    texts = {item.text for item in summary.items}
    assert "TEST101 first chunk" in texts
    assert "TEST102 only chunk" in texts
    assert "Legacy collection chunk" in texts


def test_read_chunks_includes_legacy_collection_for_course_query(populated_read_store: Path) -> None:
    summary = read_module.read_chunks(populated_read_store, course_id="LEGACY")
    texts = {item.text for item in summary.items}
    assert texts == {"Legacy collection chunk"}
    assert all(item.metadata.get("course_id") == "LEGACY" for item in summary.items)
