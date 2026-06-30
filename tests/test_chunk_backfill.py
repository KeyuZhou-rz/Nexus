"""Phase M1-2: ChromaDB → SQLite backfill tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import chromadb
import pytest

from nexus.knowledge.chunk_backfill import ChunkBackfill, _split_csv
from nexus.knowledge.sqlite_store import SQLiteStore


# ──────────────────────────────────────────────────
# _split_csv helper
# ──────────────────────────────────────────────────


def test_split_csv_comma_separated():
    assert _split_csv("a, b, c") == ["a", "b", "c"]


def test_split_csv_single_value():
    assert _split_csv("  deadlock  ") == ["deadlock"]


def test_split_csv_empty_string():
    assert _split_csv("") == []


def test_split_csv_none():
    assert _split_csv(None) == []


def test_split_csv_already_list():
    assert _split_csv(["a", "b", "c"]) == ["a", "b", "c"]


def test_split_csv_mixed_spacing():
    assert _split_csv("process, 死锁 ,  kernel  ,") == ["process", "死锁", "kernel"]


# ──────────────────────────────────────────────────
# ChunkBackfill integration tests
# ──────────────────────────────────────────────────


@pytest.fixture
def test_env(tmp_path: Path) -> tuple[Path, Path]:
    """
    创建临时 ChromaDB + SQLite 环境，写入测试 chunks。
    返回 (chroma_path, sqlite_path)。
    """
    chroma_path = tmp_path / "chroma_test"
    sqlite_path = tmp_path / "test.db"

    # 创建 ChromaDB collection 并写测试数据
    client = chromadb.PersistentClient(path=str(chroma_path))
    col = client.create_collection("TEST_CS101")

    # 3 chunks from 2 documents
    ids = ["TEST_CS101_docA_000", "TEST_CS101_docA_001", "TEST_CS101_docB_000"]
    metadatas = [
        {
            "course_id": "TEST_CS101",
            "source_file": "lecture01.pdf",
            "topic": "Introduction to Testing",
            "type": "definition",
            "keywords": "test,unit,assert,测试,单元",
            "prerequisites": "",
            "page": 1,
            "lecture_number": 1,
        },
        {
            "course_id": "TEST_CS101",
            "source_file": "lecture01.pdf",
            "topic": "Test Fixtures",
            "type": "technique",
            "keywords": "fixture,setup,teardown,pytest",
            "prerequisites": "test basics,unit testing",
            "page": 2,
            "lecture_number": 1,
        },
        {
            "course_id": "TEST_CS101",
            "source_file": "lecture02.pdf",
            "topic": "Mocking",
            "type": "technique",
            "keywords": "mock,patch,stub,模拟",
            "prerequisites": "test fixtures",
            "page": 1,
            "lecture_number": 2,
        },
    ]
    col.add(ids=ids, metadatas=metadatas, embeddings=[[0.1] * 384] * 3)

    return chroma_path, sqlite_path


def test_backfill_populates_chunks(test_env: tuple[Path, Path]):
    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)

    stats = backfill.run()
    assert stats["TEST_CS101"]["chunks"] == 3
    assert stats["TEST_CS101"]["docs"] == 2

    # Verify SQLite
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert count == 3
    conn.close()


def test_backfill_writes_documents(test_env: tuple[Path, Path]):
    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)

    backfill.run()

    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    docs = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
    assert len(docs) == 2
    assert docs[0]["id"] == "TEST_CS101/lecture01.pdf"
    assert docs[0]["chunk_count"] == 2
    assert docs[1]["id"] == "TEST_CS101/lecture02.pdf"
    assert docs[1]["chunk_count"] == 1
    conn.close()


def test_backfill_is_idempotent(test_env: tuple[Path, Path]):
    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)

    # Run twice
    stats1 = backfill.run()
    stats2 = backfill.run()

    assert stats1 == stats2
    conn = sqlite3.connect(str(sqlite_path))
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert count == 3  # No duplicates
    conn.close()


def test_backfill_chunk_fields(test_env: tuple[Path, Path]):
    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)

    backfill.run()

    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    chunk = conn.execute(
        "SELECT * FROM chunks WHERE chunk_id = 'TEST_CS101_docA_000'"
    ).fetchone()
    assert chunk is not None
    assert chunk["course_id"] == "TEST_CS101"
    assert chunk["document_id"] == "TEST_CS101/lecture01.pdf"
    assert chunk["topic"] == "Introduction to Testing"
    assert chunk["type"] == "definition"
    assert "test" in chunk["keywords"]
    assert chunk["page"] == 1
    assert chunk["lecture_number"] == 1
    conn.close()


def test_backfill_empty_collection(tmp_path: Path):
    """ChromaDB collection with no chunks should complete without error."""
    chroma_path = tmp_path / "chroma_empty"
    sqlite_path = tmp_path / "empty.db"

    client = chromadb.PersistentClient(path=str(chroma_path))
    client.create_collection("EMPTY_COURSE")

    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    stats = backfill.run()

    assert stats["EMPTY_COURSE"]["chunks"] == 0
    assert stats["EMPTY_COURSE"]["docs"] == 0


def test_backfill_skips_nexus_chunks_collection(tmp_path: Path):
    """The default 'nexus_chunks' collection should be skipped."""
    chroma_path = tmp_path / "chroma_skip"
    sqlite_path = tmp_path / "skip.db"

    client = chromadb.PersistentClient(path=str(chroma_path))
    # Create nexus_chunks with some data — should be skipped
    col = client.create_collection("nexus_chunks")
    col.add(
        ids=["nx_1"],
        metadatas=[{"course_id": "skip", "source_file": "x.pdf", "topic": "x"}],
        embeddings=[[0.1] * 384],
    )

    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    stats = backfill.run()

    assert "nexus_chunks" not in stats


def test_backfill_course_id_filter(test_env: tuple[Path, Path]):
    """--course-id should only backfill the specified collection."""
    chroma_path, sqlite_path = test_env
    # Add a second collection
    client = chromadb.PersistentClient(path=str(chroma_path))
    col2 = client.create_collection("OTHER_COURSE")
    col2.add(
        ids=["OTHER_000"],
        metadatas=[{
            "course_id": "OTHER_COURSE",
            "source_file": "misc.pdf",
            "topic": "Other Topic",
            "type": "definition",
            "keywords": "a,b",
            "prerequisites": "",
            "page": 1,
        }],
        embeddings=[[0.1] * 384],
    )

    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    stats = backfill.run(course_id="OTHER_COURSE")

    assert "OTHER_COURSE" in stats
    assert stats["OTHER_COURSE"]["chunks"] == 1
    # TEST_CS101 should NOT be backfilled
    assert "TEST_CS101" not in stats


def test_backfill_chunk_without_metadata(test_env: tuple[Path, Path]):
    """Chunks with None metadata are safely skipped."""
    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)

    # Re-get collection and add a bad entry
    client = chromadb.PersistentClient(path=str(chroma_path))
    col = client.get_collection("TEST_CS101")
    col.add(
        ids=["TEST_CS101_bad_000"],
        metadatas=[None],
        embeddings=[[0.1] * 384],
    )

    stats = backfill.run()
    # Should still have the original 3 good chunks
    conn = sqlite3.connect(str(sqlite_path))
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert count == 3  # The None-meta chunk skipped
    conn.close()


# ──────────────────────────────────────────────────
# M1-4: Concept extraction tests (stub LLM)
# ──────────────────────────────────────────────────


def test_extraction_pass1_only(test_env: tuple[Path, Path]):
    """Without LLM key, Pass 1 should produce concepts and chunk_concepts."""
    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    backfill.run()  # backfill chunks first

    stats = backfill.run_extraction("TEST_CS101", llm_api_key=None)
    assert stats is not None
    assert stats["concepts"] > 0
    assert stats["relations"] == 0  # No LLM → no relations

    # Verify persistence
    conn = sqlite3.connect(str(sqlite_path))
    cc = conn.execute("SELECT COUNT(*) FROM chunk_concepts").fetchone()[0]
    assert cc > 0
    conn.close()


def test_extraction_with_stub_llm(test_env: tuple[Path, Path], monkeypatch):
    """With stub LLM, Pass 2 should produce concepts + relations."""
    from nexus.knowledge import concept_extractor

    # Monkeypatch the LLM call to return synthetic refined data
    def _stub_refine(self, concepts, chunks, chunk_ids, course_id):
        from nexus.knowledge.concept_extractor import (
            ExtractedConcept,
            ExtractedRelation,
        )
        refined = [
            ExtractedConcept(
                concept_id="test_cs__stub_concept",
                name="Stub Concept",
                course_id=course_id,
                definition="A stub concept for testing.",
                concept_type="fundamental",
                source_chunk_ids=chunk_ids[:2],
            ),
        ]
        # Keep original candidates too
        for c in concepts:
            refined.append(c)
        relations = [
            ExtractedRelation(
                source_id="test_cs__stub_concept",
                target_id=concepts[0].concept_id if concepts else "test_cs__any",
                relation_type="prerequisite",
                confidence=0.9,
                evidence="Stub evidence",
            ),
        ]
        return refined, relations

    monkeypatch.setattr(
        concept_extractor.ConceptExtractor,
        "_call_refine_llm",
        _stub_refine,
    )

    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    backfill.run()

    stats = backfill.run_extraction("TEST_CS101", llm_api_key="stub-key")
    assert stats is not None
    assert stats["concepts"] > 0
    assert stats["relations"] > 0  # Stub LLM gives relations

    # Verify persistence
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    cr = conn.execute("SELECT COUNT(*) FROM concept_relations").fetchone()[0]
    assert cr > 0
    conn.close()


def test_extraction_empty_course(tmp_path: Path):
    """Extraction on course with no chunks returns None."""
    chroma_path = tmp_path / "chroma_empty_ex"
    sqlite_path = tmp_path / "empty_ex.db"

    client = chromadb.PersistentClient(path=str(chroma_path))
    client.create_collection("EMPTY_EX")  # empty collection, no chunks

    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    backfill.run()

    stats = backfill.run_extraction("EMPTY_EX")
    assert stats is None  # No chunks → skipped


def test_extraction_graph_retriever_has_content(test_env: tuple[Path, Path], monkeypatch):
    """After extraction with stub LLM, GraphRetriever should report has_content=True."""
    from nexus.knowledge import concept_extractor
    from nexus.knowledge.graph_retriever import GraphRetriever

    def _stub_refine(self, concepts, chunks, chunk_ids, course_id):
        from nexus.knowledge.concept_extractor import (
            ExtractedConcept,
            ExtractedRelation,
        )
        if not concepts:
            return [], []
        refined = [
            ExtractedConcept(
                concept_id="test_cs__prereq_a",
                name="Prerequisite A",
                course_id=course_id,
                definition="A prerequisite concept.",
                concept_type="fundamental",
                source_chunk_ids=chunk_ids[:1],
            ),
            ExtractedConcept(
                concept_id="test_cs__main_b",
                name="Main Concept B",
                course_id=course_id,
                definition="The main concept.",
                concept_type="fundamental",
                source_chunk_ids=chunk_ids[1:2] if len(chunk_ids) > 1 else chunk_ids[:1],
            ),
        ]
        for c in concepts:
            refined.append(c)
        relations = [
            ExtractedRelation(
                source_id="test_cs__prereq_a",
                target_id="test_cs__main_b",
                relation_type="prerequisite",
                confidence=0.95,
                evidence="B requires A",
            ),
        ]
        return refined, relations

    monkeypatch.setattr(
        concept_extractor.ConceptExtractor,
        "_call_refine_llm",
        _stub_refine,
    )

    chroma_path, sqlite_path = test_env
    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(chroma_path, store)
    backfill.run()
    backfill.run_extraction("TEST_CS101", llm_api_key="stub-key")

    retriever = GraphRetriever(store)
    ctx = retriever.retrieve(["TEST_CS101_docA_000"], course_id="TEST_CS101")
    assert ctx.has_content is True
    assert len(ctx.prerequisites) >= 1
