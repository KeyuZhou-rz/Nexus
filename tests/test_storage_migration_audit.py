from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from nexus.knowledge.chunk_backfill import KnowledgeConsistencyAudit
from nexus.knowledge.sqlite_store import SQLiteStore, connect


def test_legacy_database_is_migrated_and_stale_parsing_recovered(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE documents (
                id TEXT PRIMARY KEY, course_id TEXT NOT NULL, filename TEXT NOT NULL,
                file_hash TEXT NOT NULL, parsed_at TIMESTAMP,
                chunk_count INTEGER DEFAULT 0, parse_model TEXT,
                parse_status TEXT DEFAULT 'pending'
            )"""
        )
        conn.execute(
            """INSERT INTO documents(id, course_id, filename, file_hash, parsed_at, parse_status)
               VALUES ('OS/old.pdf', 'OS', 'old.pdf', 'hash', datetime('now', '-2 hours'), 'parsing')"""
        )

    store = SQLiteStore(db_path)
    assert store.schema_version == 1
    with connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert "status_updated_at" in columns
    assert store.recover_stale_parsing() == 1
    assert store.get_document("OS/old.pdf")["parse_status"] == "pending"


class _Collection:
    name = "CS101"
    metadata = {"course_id": "CS101"}

    def get(self, **_kwargs):
        return {
            "ids": ["chroma-only"],
            "metadatas": [{"course_id": "CS101", "source_file": "lecture.pdf"}],
        }


class _Client:
    def list_collections(self):
        return [_Collection()]


def test_consistency_audit_reports_cross_store_drift_without_writing(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "nexus.db"
    store = SQLiteStore(db_path)
    store.upsert_document("CS101/lecture.pdf", "CS101", "lecture.pdf", "hash", parse_status="done", chunk_count=1)
    store.upsert_chunk("sqlite-only", "CS101/lecture.pdf", "CS101", "Topic")
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=lambda **_: _Client()))

    report = KnowledgeConsistencyAudit(db_path, tmp_path / "chroma").run()
    codes = {issue["code"] for issue in report["issues"]}
    assert not report["ok"]
    assert "chroma_chunk_missing_sqlite" in codes
    assert "sqlite_chunk_missing_chroma" in codes
    assert store.schema_version == 1
