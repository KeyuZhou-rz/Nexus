"""Operational backup, restore, and conservative index repair helpers."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


BACKUP_FORMAT_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_conn, sqlite3.connect(destination) as dest_conn:
        source_conn.backup(dest_conn)
    with sqlite3.connect(destination) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite snapshot failed integrity check: {result}")


def create_backup(
    sqlite_path: Path,
    chroma_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Create a checksummed ZIP containing a consistent SQLite snapshot and Chroma files."""
    sqlite_path = sqlite_path.expanduser().resolve()
    chroma_path = chroma_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if output_path == sqlite_path or output_path.is_relative_to(chroma_path):
        raise ValueError("Backup output must be outside the source database and Chroma directory")
    if output_path.exists() and not force:
        raise FileExistsError(f"Backup already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="nexus-backup-") as temp_dir:
        temp_root = Path(temp_dir)
        snapshot = temp_root / "sqlite" / "nexus.db"
        _sqlite_snapshot(sqlite_path, snapshot)

        files: dict[str, str] = {"sqlite/nexus.db": _sha256(snapshot)}
        if chroma_path.is_dir():
            for source in sorted(
                path for path in chroma_path.rglob("*")
                if path.is_file() and not path.is_symlink()
            ):
                relative = PurePosixPath("chroma") / PurePosixPath(source.relative_to(chroma_path).as_posix())
                files[str(relative)] = _sha256(source)

        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "chroma_present": chroma_path.is_dir(),
            "files": files,
        }
        staged_output = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(staged_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, "sqlite/nexus.db")
                if chroma_path.is_dir():
                    for relative in sorted(key for key in files if key.startswith("chroma/")):
                        source = chroma_path / Path(PurePosixPath(relative).relative_to("chroma"))
                        archive.write(source, relative)
                archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            os.replace(staged_output, output_path)
        finally:
            staged_output.unlink(missing_ok=True)

    return {"ok": True, "archive": str(output_path), "files": len(files), "manifest": manifest}


def _validate_archive(archive_path: Path, extraction_root: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe archive member: {name}")
        if "manifest.json" not in names:
            raise ValueError("Backup has no manifest.json")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError(f"Unsupported backup format: {manifest.get('format_version')}")
        expected_files = manifest.get("files")
        if not isinstance(expected_files, dict) or "sqlite/nexus.db" not in expected_files:
            raise ValueError("Backup manifest has no SQLite snapshot")
        for relative, expected_hash in expected_files.items():
            if relative not in names:
                raise ValueError(f"Backup member missing: {relative}")
            target = extraction_root / Path(PurePosixPath(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(relative) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            if _sha256(target) != expected_hash:
                raise ValueError(f"Checksum mismatch: {relative}")
        if manifest.get("chroma_present"):
            (extraction_root / "chroma").mkdir(exist_ok=True)

    with sqlite3.connect(extraction_root / "sqlite" / "nexus.db") as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ValueError(f"Backup SQLite integrity check failed: {integrity}")
    return manifest


def restore_backup(
    archive_path: Path,
    sqlite_path: Path,
    chroma_path: Path,
    *,
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Validate a backup, and replace target stores only when ``apply`` is explicit."""
    archive_path = archive_path.expanduser().resolve()
    sqlite_path = sqlite_path.expanduser().resolve()
    chroma_path = chroma_path.expanduser().resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"Backup not found: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="nexus-restore-") as temp_dir:
        extraction_root = Path(temp_dir)
        manifest = _validate_archive(archive_path, extraction_root)
        conflicts = [str(path) for path in (sqlite_path, chroma_path) if path.exists()]
        plan = {
            "ok": True,
            "applied": False,
            "archive": str(archive_path),
            "targets": {"sqlite": str(sqlite_path), "chroma": str(chroma_path)},
            "conflicts": conflicts,
            "files": len(manifest["files"]),
        }
        if not apply:
            return plan
        if conflicts and not force:
            raise FileExistsError("Restore targets exist; rerun with --force to replace them")

        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        chroma_path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        rollback: list[tuple[Path, Path]] = []
        staged_sqlite_target = sqlite_path.with_name(f".{sqlite_path.name}.restore-{token}")
        staged_chroma_target = chroma_path.with_name(f".{chroma_path.name}.restore-{token}")
        try:
            shutil.copy2(extraction_root / "sqlite" / "nexus.db", staged_sqlite_target)
            if manifest.get("chroma_present"):
                shutil.copytree(extraction_root / "chroma", staged_chroma_target)
            for target in (sqlite_path, sqlite_path.with_name(sqlite_path.name + "-wal"), sqlite_path.with_name(sqlite_path.name + "-shm"), chroma_path):
                if target.exists():
                    saved = target.with_name(f".{target.name}.pre-restore-{token}")
                    os.replace(target, saved)
                    rollback.append((target, saved))
            os.replace(staged_sqlite_target, sqlite_path)
            if manifest.get("chroma_present"):
                os.replace(staged_chroma_target, chroma_path)
        except Exception:
            if sqlite_path.exists():
                sqlite_path.unlink()
            if chroma_path.exists():
                shutil.rmtree(chroma_path)
            for target, saved in reversed(rollback):
                os.replace(saved, target)
            raise
        else:
            for _, saved in rollback:
                if saved.is_dir():
                    shutil.rmtree(saved)
                else:
                    saved.unlink(missing_ok=True)
        finally:
            staged_sqlite_target.unlink(missing_ok=True)
            if staged_chroma_target.exists():
                shutil.rmtree(staged_chroma_target)
        plan["applied"] = True
        return plan


def repair_consistency(
    sqlite_path: Path,
    chroma_path: Path,
    *,
    apply: bool = False,
    audit_factory: Callable[[Path, Path], Any] | None = None,
) -> dict[str, Any]:
    """Repair only deterministic SQLite chunk-count drift; report all other issues."""
    if audit_factory is None:
        from .chunk_backfill import KnowledgeConsistencyAudit

        audit_factory = KnowledgeConsistencyAudit
    before = audit_factory(sqlite_path, chroma_path).run()
    repairable = [
        issue for issue in before.get("issues", [])
        if issue.get("code") == "document_chunk_count_mismatch" and issue.get("document_id")
    ]
    blocked = [issue for issue in before.get("issues", []) if issue not in repairable]
    result = {
        "ok": before.get("ok", False),
        "applied": False,
        "repairable": repairable,
        "unresolved": blocked,
        "before": before.get("summary", {}),
    }
    if apply and repairable:
        with sqlite3.connect(sqlite_path) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            for issue in repairable:
                conn.execute(
                    """UPDATE documents SET chunk_count=(SELECT COUNT(*) FROM chunks WHERE document_id=?)
                       WHERE id=?""",
                    (issue["document_id"], issue["document_id"]),
                )
        result["applied"] = True
        after = audit_factory(sqlite_path, chroma_path).run()
        result["ok"] = after.get("ok", False)
        result["after"] = after.get("summary", {})
        result["unresolved"] = after.get("issues", [])
    return result
