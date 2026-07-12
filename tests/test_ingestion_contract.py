from __future__ import annotations

from pathlib import Path

import pytest

from nexus.knowledge import ingestor as ingestor_module
from nexus.knowledge.ingestion_service import content_hash
from nexus.knowledge.ingestion_service import TextDocument, ingest_text_documents
from nexus.knowledge.ingestor import CourseIngestor
from nexus.knowledge.parser import ParsedChunk, ParseResult
from nexus.knowledge.sqlite_store import SQLiteStore


class _ChromaStub:
    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.metadatas: list[dict] = []
        self.fail_upsert = False
        self.fail_delete = False

    def upsert_chunks(self, ids, _texts, metadatas, *, course_id):
        self.ids.update(ids)
        self.metadatas = metadatas
        if self.fail_upsert:
            raise RuntimeError("vector write failed")

    def delete_chunks_by_ids(self, ids, *, course_id):
        if self.fail_delete:
            raise RuntimeError("vector cleanup failed")
        self.ids.difference_update(ids)

    def has_chunk_ids(self, ids, *, course_id):
        return set(ids) <= self.ids


def _ingestor(tmp_path: Path, chroma: _ChromaStub) -> CourseIngestor:
    instance = CourseIngestor.__new__(CourseIngestor)
    instance.llm_api_key = None
    instance._sqlite = SQLiteStore(tmp_path / "nexus.db")
    instance._chroma = chroma
    instance._extract_concepts = lambda _chunks, _ids, _course: {"concepts": 0, "relations": 0}
    return instance


def _parse_result() -> ParseResult:
    return ParseResult(
        chunks=[
            ParsedChunk(
                topic="Process",
                content="A process is a running program.",
                type="definition",
                keywords=["process"],
                page=3,
            )
        ],
        parse_method="text_fallback",
    )


def test_failed_dual_write_is_retryable_and_never_done(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "lecture.pdf"
    source.write_bytes(b"course material")
    chroma = _ChromaStub()
    chroma.fail_upsert = True
    chroma.fail_delete = True
    ingestor = _ingestor(tmp_path, chroma)
    monkeypatch.setattr(ingestor_module, "parse_document", lambda _path: _parse_result())

    failed = ingestor.ingest_file(source, "CS101")
    document = ingestor._sqlite.get_document("CS101/lecture.pdf")
    assert failed.status == "failed"
    assert document["parse_status"] == "failed"
    assert ingestor._sqlite.get_chunks_by_course("CS101")

    chroma.fail_delete = False
    chroma.fail_upsert = False
    retried = ingestor.ingest_file(source, "CS101")
    assert retried.status == "ingested"
    assert ingestor._sqlite.get_document("CS101/lecture.pdf")["parse_status"] == "done"
    assert len(ingestor._sqlite.get_chunks_by_course("CS101")) == 1

    skipped = ingestor.ingest_file(source, "CS101")
    assert skipped.status == "skipped"


def test_canonical_metadata_and_ids_match_both_stores(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "lecture.pdf"
    source.write_bytes(b"same bytes")
    chroma = _ChromaStub()
    ingestor = _ingestor(tmp_path, chroma)
    monkeypatch.setattr(ingestor_module, "parse_document", lambda _path: _parse_result())

    assert ingestor.ingest_file(source, "CS101").status == "ingested"
    sqlite_chunk = ingestor._sqlite.get_chunks_by_course("CS101")[0]
    metadata = chroma.metadatas[0]
    expected_hash = content_hash(b"same bytes")

    assert metadata["chunk_id"] == sqlite_chunk["chunk_id"]
    assert metadata["document_id"] == sqlite_chunk["document_id"] == "CS101/lecture.pdf"
    assert metadata["source_file"] == metadata["file_name"] == "lecture.pdf"
    assert metadata["course_id"] == "CS101"
    assert metadata["file_hash"] == expected_hash
    assert metadata["chunk_index"] == 0


def test_document_upsert_does_not_cascade_delete_existing_chunks(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_document("CS101/a.pdf", "CS101", "a.pdf", "old")
    store.upsert_chunk("chunk-1", "CS101/a.pdf", "CS101", "Topic")

    store.upsert_document("CS101/a.pdf", "CS101", "a.pdf", "new", parse_status="done", chunk_count=1)

    assert [row["chunk_id"] for row in store.get_chunks_by_course("CS101")] == ["chunk-1"]


def test_legacy_wrapper_cannot_override_canonical_metadata(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("content", encoding="utf-8")
    document = TextDocument(
        source_path=source,
        text="content",
        course_id="CS101",
        doc_type="notes",
        timestamp="2026-01-01",
        extra_metadata={"course_id": "WRONG"},
    )

    class _Store:
        def upsert_chunks(self, *_args, **_kwargs):
            raise AssertionError("invalid metadata must fail before writing")

    with pytest.raises(ValueError, match="canonical fields"):
        ingest_text_documents([document], _Store())
