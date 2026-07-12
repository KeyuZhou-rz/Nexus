from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from nexus.knowledge.maintenance import create_backup, repair_consistency, restore_backup


def _database(path: Path, value: str = "original") -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES (?)", (value,))


def test_backup_and_restore_round_trip_with_dry_run(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    source_chroma = tmp_path / "source-chroma"
    source_chroma.mkdir()
    (source_chroma / "index.bin").write_bytes(b"vector-data")
    _database(source_db)
    archive = tmp_path / "backup.zip"

    backup = create_backup(source_db, source_chroma, archive)
    assert backup["ok"]
    assert backup["files"] == 2

    target_db = tmp_path / "target" / "nexus.db"
    target_chroma = tmp_path / "target" / "chroma"
    preview = restore_backup(archive, target_db, target_chroma)
    assert not preview["applied"]
    assert not target_db.exists()

    restored = restore_backup(archive, target_db, target_chroma, apply=True)
    assert restored["applied"]
    with sqlite3.connect(target_db) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "original"
    assert (target_chroma / "index.bin").read_bytes() == b"vector-data"


def test_restore_requires_force_to_replace_existing_targets(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    _database(source_db)
    archive = tmp_path / "backup.zip"
    create_backup(source_db, tmp_path / "missing-chroma", archive)

    target_db = tmp_path / "target.db"
    _database(target_db, "keep")
    with pytest.raises(FileExistsError):
        restore_backup(archive, target_db, tmp_path / "chroma", apply=True)
    with sqlite3.connect(target_db) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "keep"

    restore_backup(archive, target_db, tmp_path / "chroma", apply=True, force=True)
    with sqlite3.connect(target_db) as conn:
        assert conn.execute("SELECT value FROM sample").fetchone()[0] == "original"


def test_restore_rejects_checksum_mismatch(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    _database(source_db)
    archive = tmp_path / "backup.zip"
    create_backup(source_db, tmp_path / "chroma", archive)

    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "w") as destination:
        for name in source.namelist():
            payload = b"not-a-database" if name == "sqlite/nexus.db" else source.read(name)
            destination.writestr(name, payload)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        restore_backup(corrupt, tmp_path / "restored.db", tmp_path / "restored-chroma")


def test_repair_is_dry_run_and_only_updates_document_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "nexus.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE documents(id TEXT PRIMARY KEY, chunk_count INTEGER);
            CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT);
            INSERT INTO documents VALUES ('doc', 9);
            INSERT INTO chunks VALUES ('chunk', 'doc');
            """
        )

    class Audit:
        def __init__(self, sqlite_path: Path, _chroma_path: Path) -> None:
            self.sqlite_path = sqlite_path

        def run(self) -> dict:
            with sqlite3.connect(self.sqlite_path) as conn:
                count = conn.execute("SELECT chunk_count FROM documents WHERE id='doc'").fetchone()[0]
            issues = [] if count == 1 else [
                {
                    "code": "document_chunk_count_mismatch",
                    "message": "wrong count",
                    "document_id": "doc",
                },
                {"code": "sqlite_chunk_missing_chroma", "message": "manual action required"},
            ]
            return {"ok": not issues, "summary": {"issues": len(issues)}, "issues": issues}

    preview = repair_consistency(db_path, tmp_path / "chroma", audit_factory=Audit)
    assert not preview["applied"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT chunk_count FROM documents").fetchone()[0] == 9

    result = repair_consistency(db_path, tmp_path / "chroma", apply=True, audit_factory=Audit)
    assert result["applied"]
    assert result["ok"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT chunk_count FROM documents").fetchone()[0] == 1
