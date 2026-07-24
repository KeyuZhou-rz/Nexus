from __future__ import annotations

import sys
from pathlib import Path

from nexus.cli import db_repair
from nexus.knowledge import chunk_backfill


class _AuditOK:
    def __init__(self, _sqlite_path: Path, _chroma_path: Path) -> None:
        pass

    def run(self) -> dict:
        return {"ok": True, "summary": {"issues": 0}, "issues": []}


class _WriteMigration(chunk_backfill.SafeMigration):
    def __init__(self, sqlite_path: Path, chroma_path: Path) -> None:
        super().__init__(sqlite_path, chroma_path)
        self.writes = 0

    def plan(self) -> dict:
        return {"ok": True, "dry_run": True}

    def apply(self) -> dict:
        self.writes += 1
        return {"ok": True, "applied": True}


def test_safe_migration_creates_backup_before_applying_and_post_audits(
    tmp_path: Path, monkeypatch
) -> None:
    sqlite_path = tmp_path / "nexus.db"
    sqlite_path.touch()
    migration = _WriteMigration(sqlite_path, tmp_path / "chroma")
    backup_paths: list[Path] = []

    def fake_backup(*, output_path: Path, **_kwargs) -> dict:
        backup_paths.append(output_path)
        output_path.touch()
        return {"ok": True, "archive": str(output_path)}

    monkeypatch.setattr("nexus.knowledge.maintenance.create_backup", fake_backup)
    monkeypatch.setattr(chunk_backfill, "KnowledgeConsistencyAudit", _AuditOK)

    report = migration.safe_apply()

    assert migration.writes == 1
    assert report["ok"] is True
    assert report["applied"] is True
    assert report["post_audit"]["ok"] is True
    assert backup_paths and backup_paths[0].parent == tmp_path / "backups"
    assert report["backup_path"] == str(backup_paths[0])


def test_safe_migration_refuses_writes_when_backup_fails(tmp_path: Path, monkeypatch) -> None:
    migration = _WriteMigration(tmp_path / "nexus.db", tmp_path / "chroma")
    monkeypatch.setattr(migration, "pre_backup", lambda: None)

    report = migration.safe_apply()

    assert migration.writes == 0
    assert report["ok"] is False
    assert report["applied"] is False
    assert report["post_audit"] is None


def test_safe_migration_post_audits_a_failed_write(tmp_path: Path, monkeypatch) -> None:
    migration = _WriteMigration(tmp_path / "nexus.db", tmp_path / "chroma")
    monkeypatch.setattr(migration, "pre_backup", lambda: tmp_path / "backup.zip")
    monkeypatch.setattr(migration, "post_verify", lambda: {"ok": True, "issues": []})

    def fail() -> dict:
        raise RuntimeError("write interrupted")

    report = migration._safe_execute(fail)

    assert report["ok"] is False
    assert report["migration_result"]["error"] == "write interrupted"
    assert report["post_audit"] == {"ok": True, "issues": 0, "summary": {}}


def test_db_repair_apply_routes_every_write_path_through_safe_wrapper(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[str, object]] = []

    class Metadata:
        def __init__(self, *_args) -> None:
            pass

        def plan(self) -> dict:
            raise AssertionError("--apply must not call plan")

        def safe_apply(self) -> dict:
            calls.append(("metadata", None))
            return {"ok": True}

    class Quarantine:
        def __init__(self, *_args) -> None:
            pass

        def plan(self) -> dict:
            raise AssertionError("--apply must not call plan")

        def safe_apply(self, *, categories=None) -> dict:
            calls.append(("quarantine", categories))
            return {"ok": True}

        def safe_restore(self, concept_id: str) -> dict:
            calls.append(("restore", concept_id))
            return {"ok": True}

    class Repair:
        def __init__(self, *_args) -> None:
            pass

        def plan(self) -> dict:
            raise AssertionError("--apply must not call plan")

        def safe_apply(self) -> dict:
            calls.append(("repair", None))
            return {"ok": True}

    monkeypatch.setattr(chunk_backfill, "MetadataMigration", Metadata)
    monkeypatch.setattr(chunk_backfill, "ConceptQuarantine", Quarantine)
    monkeypatch.setattr(chunk_backfill, "ConsistencyRepairMigration", Repair)
    sqlite_path = tmp_path / "nexus.db"
    chroma_path = tmp_path / "chroma"

    monkeypatch.setattr(
        sys,
        "argv",
        ["db-repair", "--sqlite-path", str(sqlite_path), "--db-dir", str(chroma_path), "--migrate-metadata", "--apply"],
    )
    assert db_repair.main() == 0

    monkeypatch.setattr(
        sys,
        "argv",
        ["db-repair", "--sqlite-path", str(sqlite_path), "--db-dir", str(chroma_path), "--quarantine-concepts", "--apply", "--categories", "pseudo"],
    )
    assert db_repair.main() == 0

    monkeypatch.setattr(
        sys,
        "argv",
        ["db-repair", "--sqlite-path", str(sqlite_path), "--db-dir", str(chroma_path), "--restore", "concept-1"],
    )
    assert db_repair.main() == 0

    monkeypatch.setattr(
        sys,
        "argv",
        ["db-repair", "--sqlite-path", str(sqlite_path), "--db-dir", str(chroma_path), "--apply"],
    )
    assert db_repair.main() == 0

    assert calls == [
        ("metadata", None),
        ("quarantine", ["pseudo"]),
        ("restore", "concept-1"),
        ("repair", None),
    ]


def test_safe_migration_dry_run_is_not_wrapped_or_backed_up(tmp_path: Path) -> None:
    migration = _WriteMigration(tmp_path / "nexus.db", tmp_path / "chroma")

    assert migration.plan() == {"ok": True, "dry_run": True}
    assert migration.writes == 0
    assert not (tmp_path / "backups").exists()
