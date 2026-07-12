from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nexus.knowledge import maintenance as maintenance_module
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


def test_backup_hashes_and_archives_only_staged_chroma(
    tmp_path: Path, monkeypatch
) -> None:
    source_db = tmp_path / "nexus.db"
    _database(source_db)
    source_chroma = tmp_path / "chroma"
    source_chroma.mkdir()
    payload = source_chroma / "payload.bin"
    payload.write_bytes(b"snapshot-version")
    archive = tmp_path / "backup.zip"
    real_sha256 = maintenance_module._sha256
    mutated = False

    def mutate_source_after_staging(path: Path) -> str:
        nonlocal mutated
        if path.name == "payload.bin" and path != payload and not mutated:
            payload.write_bytes(b"changed-after-staging")
            mutated = True
        return real_sha256(path)

    monkeypatch.setattr(maintenance_module, "_sha256", mutate_source_after_staging)
    create_backup(source_db, source_chroma, archive)

    with zipfile.ZipFile(archive) as backup:
        archived_payload = backup.read("chroma/payload.bin")
        manifest = json.loads(backup.read("manifest.json"))
    assert mutated
    assert archived_payload == b"snapshot-version"
    assert manifest["files"]["chroma/payload.bin"] == hashlib.sha256(
        archived_payload
    ).hexdigest()


def test_backup_waits_for_cross_process_ingest_lock(tmp_path: Path) -> None:
    source_db = tmp_path / "nexus.db"
    _database(source_db)
    source_chroma = tmp_path / "chroma"
    source_chroma.mkdir()
    payload = source_chroma / "payload.bin"
    payload.write_bytes(b"before")
    ready = tmp_path / "writer-ready"
    release = tmp_path / "writer-release"
    script = """
import sys
import time
from pathlib import Path
from nexus.knowledge.maintenance_lock import maintenance_lock

db, chroma, payload, ready, release = map(Path, sys.argv[1:])
with maintenance_lock(sqlite_path=db, chroma_path=chroma, exclusive=False):
    payload.write_bytes(b'writing')
    ready.touch()
    deadline = time.time() + 15
    while not release.exists():
        if time.time() > deadline:
            raise TimeoutError('release gate not opened')
        time.sleep(0.005)
    payload.write_bytes(b'stable')
"""
    writer = subprocess.Popen(
        [
            sys.executable, "-c", script, str(source_db), str(source_chroma),
            str(payload), str(ready), str(release),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 15
    while not ready.exists():
        if time.time() > deadline:
            writer.kill()
            raise TimeoutError("writer did not acquire shared maintenance lock")
        time.sleep(0.005)

    archive = tmp_path / "concurrent.zip"
    with ThreadPoolExecutor(max_workers=1) as executor:
        backup = executor.submit(
            create_backup, source_db, source_chroma, archive
        )
        time.sleep(0.05)
        assert not backup.done()
        release.touch()
        backup.result(timeout=30)
    stdout, stderr = writer.communicate(timeout=30)
    assert writer.returncode == 0, (stdout, stderr)
    with zipfile.ZipFile(archive) as result:
        assert result.read("chroma/payload.bin") == b"stable"


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
