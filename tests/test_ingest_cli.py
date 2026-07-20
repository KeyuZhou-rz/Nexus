from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

from nexus.cli.ingest import _run_phase3, main as ingest_main
from nexus.knowledge.ingestor import IngestResult
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.store import ChromaKnowledgeStore


def _args(input_path: Path, tmp_path: Path) -> Namespace:
    return Namespace(
        input=str(input_path),
        course_id="CS_TEST",
        db_dir=str(tmp_path / "chroma"),
        sqlite_path=str(tmp_path / "nexus.db"),
        force=False,
    )


def test_phase3_ingest_returns_failure_for_any_failed_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    class FakeIngestor:
        def __init__(self, **kwargs) -> None:
            pass

        def ingest_file(self, filepath, course_id, *, force=False):
            if filepath.name == "first.pdf":
                return IngestResult(filepath, course_id, "ingested", chunks=3)
            return IngestResult(filepath, course_id, "failed", error="parse failed")

    monkeypatch.setattr("nexus.knowledge.ingestor.CourseIngestor", FakeIngestor)

    assert _run_phase3(_args(tmp_path, tmp_path)) == 1
    output = capsys.readouterr().out
    assert "[FAIL] second.pdf" in output
    assert "成功 1，跳过 0，失败 1" in output


def test_phase3_ingest_succeeds_when_files_are_ingested_or_skipped(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    class FakeIngestor:
        def __init__(self, **kwargs) -> None:
            pass

        def ingest_file(self, filepath, course_id, *, force=False):
            status = "ingested" if filepath.name == "first.pdf" else "skipped"
            return IngestResult(filepath, course_id, status, chunks=2)

    monkeypatch.setattr("nexus.knowledge.ingestor.CourseIngestor", FakeIngestor)

    assert _run_phase3(_args(tmp_path, tmp_path)) == 0
    assert "成功 1，跳过 1，失败 0" in capsys.readouterr().out


def test_legacy_ingest_dual_writes_chroma_and_sqlite(tmp_path: Path, monkeypatch) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text("Legacy ingest test content with enough text to form at least one chunk.")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nexus ingest",
            "--input", str(notes),
            "--course-id", "LEGACY101",
            "--db-dir", str(tmp_path / "chroma"),
            "--sqlite-path", str(tmp_path / "nexus.db"),
            "--legacy",
        ],
    )
    assert ingest_main() == 0

    sqlite_store = SQLiteStore(tmp_path / "nexus.db")
    documents = sqlite_store.get_chunks_by_course("LEGACY101")
    assert len(documents) >= 1
    doc_row = sqlite_store.get_document("LEGACY101/notes.md")
    assert doc_row is not None
    assert doc_row["course_id"] == "LEGACY101"
    assert doc_row["parse_status"] == "done"

    with ChromaKnowledgeStore(tmp_path / "chroma", per_course=True) as chroma_store:
        collection = chroma_store.get_course_collection("LEGACY101")
        assert collection.count() >= 1
