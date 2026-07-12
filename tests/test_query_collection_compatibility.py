from __future__ import annotations

from pathlib import Path

import chromadb
import pytest

from nexus.knowledge.query import query_knowledge
from nexus.knowledge.store import ChromaKnowledgeStore


class _TestEmbedding:
    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in input:
            normalized = text.casefold()
            vectors.append([
                float("process" in normalized),
                float("database" in normalized),
                float("legacy" in normalized),
            ])
        return vectors

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    @staticmethod
    def is_legacy() -> bool:
        return True

    @staticmethod
    def name() -> str:
        return "nexus-test-embedding"

    @staticmethod
    def get_config() -> dict:
        return {}


@pytest.fixture
def embedding(monkeypatch):
    monkeypatch.setattr(
        chromadb.utils.embedding_functions,
        "DefaultEmbeddingFunction",
        lambda: _TestEmbedding(),
    )


def _metadata(course_id: str, source: str) -> dict:
    return {
        "course_id": course_id,
        "source_file": source,
        "file_name": source,
        "doc_type": "notes",
    }


def test_query_reads_per_course_collections_without_creating_default(
    tmp_path: Path, embedding
) -> None:
    db_dir = tmp_path / "chroma"
    with ChromaKnowledgeStore(db_dir, per_course=True) as store:
        store.upsert_chunks(
            ["os-1"],
            ["A process is a running program."],
            [_metadata("CS202_OS", "os.pdf")],
            course_id="CS202_OS",
        )
        store.upsert_chunks(
            ["db-1"],
            ["A database stores structured records."],
            [_metadata("CS305_DB", "db.pdf")],
            course_id="CS305_DB",
        )

    course_result = query_knowledge(db_dir, "process", course_id="CS202_OS")
    assert [item.metadata["course_id"] for item in course_result.items] == ["CS202_OS"]

    global_result = query_knowledge(db_dir, "database", n_results=2)
    assert {item.metadata["course_id"] for item in global_result.items} == {"CS202_OS", "CS305_DB"}

    client = chromadb.PersistentClient(path=str(db_dir))
    assert {collection.name for collection in client.list_collections()} == {"CS202_OS", "CS305_DB"}


def test_query_merges_existing_legacy_default_collection(tmp_path: Path, embedding) -> None:
    db_dir = tmp_path / "chroma"
    with ChromaKnowledgeStore(db_dir) as legacy:
        legacy.upsert_chunks(
            ["legacy-1"],
            ["Legacy process notes."],
            [_metadata("CS202_OS", "legacy.md")],
        )
    with ChromaKnowledgeStore(db_dir, per_course=True) as current:
        current.upsert_chunks(
            ["current-1"],
            ["Current process lecture."],
            [_metadata("CS202_OS", "current.pdf")],
            course_id="CS202_OS",
        )

    result = query_knowledge(db_dir, "process", course_id="CS202_OS", n_results=5)

    assert {item.metadata["file_name"] for item in result.items} == {"legacy.md", "current.pdf"}


def test_unknown_course_returns_no_results_without_creating_collection(
    tmp_path: Path, embedding
) -> None:
    db_dir = tmp_path / "chroma"

    result = query_knowledge(db_dir, "unknown", course_id="MISSING")

    assert result.items == []
    client = chromadb.PersistentClient(path=str(db_dir))
    assert client.list_collections() == []
