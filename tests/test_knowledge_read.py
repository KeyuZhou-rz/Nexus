from __future__ import annotations

from pathlib import Path

from nexus.knowledge import read as read_module


class _StoreStub:
    last = None

    def __init__(self, *_args, **_kwargs):
        pass

    def fetch_chunks(self, *, where=None, limit=20):
        _StoreStub.last = {"where": where, "limit": limit}
        return {
            "documents": ["second", "first"],
            "metadatas": [
                {"file_name": "a.md", "chunk_index": "2", "course_id": "EE201"},
                {"file_name": "a.md", "chunk_index": "1", "course_id": "EE201"},
            ],
        }


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
