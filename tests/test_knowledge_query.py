from __future__ import annotations

from pathlib import Path

from nexus.knowledge import query as query_module


class _StoreStub:
    def __init__(self, *_args, **_kwargs):
        self.last = None

    def query(self, text: str, n_results: int = 5, where=None):
        self.last = {"text": text, "n_results": n_results, "where": where}
        return {
            "documents": [["chunk-1"]],
            "metadatas": [[{"file_name": "a.md", "course_id": "EE201", "doc_type": "notes"}]],
            "distances": [[0.123]],
        }


def test_query_knowledge_with_filters(monkeypatch):
    monkeypatch.setattr(query_module, "ChromaKnowledgeStore", _StoreStub)
    summary = query_module.query_knowledge(
        Path("data/chroma"),
        "op amp",
        n_results=3,
        course_id="EE201",
        doc_type="notes",
    )
    assert len(summary.items) == 1
    assert summary.items[0].metadata["course_id"] == "EE201"
    assert summary.items[0].distance == 0.123


def test_query_knowledge_without_results(monkeypatch):
    class _EmptyStore(_StoreStub):
        def query(self, text: str, n_results: int = 5, where=None):
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    monkeypatch.setattr(query_module, "ChromaKnowledgeStore", _EmptyStore)
    summary = query_module.query_knowledge(Path("data/chroma"), "x")
    assert summary.items == []
