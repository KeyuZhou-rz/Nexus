from __future__ import annotations

from pathlib import Path

from nexus.archive_sync.post_ingest import run_archive_post_ingest
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.user_profile import UserProfileManager


class _StoreStub:
    def __init__(self) -> None:
        self.calls = []

    def upsert_chunks(self, ids, texts, metadatas):
        self.calls.append((ids, texts, metadatas))


def test_run_archive_post_ingest_ingests_text_and_updates_sqlite_profile(tmp_path: Path):
    archive_file = tmp_path / "calc_notes.txt"
    archive_file.write_text(
        """
Common mistakes: chain rule sign in implicit differentiation.
A review line.
""".strip(),
        encoding="utf-8",
    )

    archives = [
        {
            "course": "MATH-UA 123",
            "archived_path": str(archive_file),
        }
    ]

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Use a real SQLite DB for profile assertions
    sqlite_path = tmp_path / "nexus.db"
    sqlite_store = SQLiteStore(sqlite_path)

    stub = _StoreStub()
    summary = run_archive_post_ingest(
        archives=archives,
        data_dir=data_dir,
        db_dir=tmp_path / "chroma",
        sqlite_path=sqlite_path,
        store=stub,
        max_chars=80,
        overlap=10,
    )

    assert summary["status"] == "success"
    assert summary["files_scanned"] == 1
    assert summary["files_ingested"] == 1
    assert summary["chunks"] >= 1
    assert summary["weak_candidates"] >= 1
    assert summary["active_weak_points"] >= 1
    assert len(stub.calls) == 1

    # Assert via SQLite user_profile (was: state.json)
    profile_mgr = UserProfileManager(sqlite_store)
    profile = profile_mgr.get()
    weak_points = profile.get("weak_points", [])
    assert any("chain rule" in wp.get("concept", "") for wp in weak_points)


def test_run_archive_post_ingest_missing_file_skipped(tmp_path: Path):
    """Archives with non-existent paths are skipped gracefully."""
    archives = [
        {"course": "MATH-UA 123", "archived_path": "/nonexistent/file.txt"},
    ]
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    summary = run_archive_post_ingest(
        archives=archives,
        data_dir=data_dir,
        db_dir=tmp_path / "chroma",
    )

    assert summary["status"] == "success"
    assert summary["files_scanned"] == 0
    assert summary["files_ingested"] == 0
    assert summary["weak_candidates"] == 0
    assert summary["active_weak_points"] == 0
