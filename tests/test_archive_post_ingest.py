from __future__ import annotations

from pathlib import Path

from nexus.archive_sync.post_ingest import run_archive_post_ingest
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.user_profile import UserProfileManager


class _StoreStub:
    def __init__(self) -> None:
        self.calls = []

    def upsert_chunks(self, ids, texts, metadatas, course_id=None):
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


def test_run_archive_post_ingest_uses_item_course_for_weak_points(tmp_path: Path, monkeypatch):
    """Weak-point candidates must carry the archive item's course, not a hard-coded value."""

    class _CourseAwareStoreStub:
        def __init__(self) -> None:
            self.calls = []

        def upsert_chunks(self, ids, texts, metadatas, course_id=None):
            self.calls.append((ids, texts, metadatas))

    cs_file = tmp_path / "cs_notes.txt"
    cs_file.write_text("Common mistakes: pointer arithmetic off by one.")
    math_file = tmp_path / "math_notes.txt"
    math_file.write_text("Common mistakes: chain rule sign error.")

    archives = [
        {"course": "CS101", "archived_path": str(cs_file)},
        {"course": "MATH201", "archived_path": str(math_file)},
    ]

    captured_courses = []

    def fake_record_weak_point(_resolver, _profile, _temporal, topic, *, course_id, context):
        captured_courses.append((course_id, topic))
        return {"resolved": True, "concept_id": f"{course_id}:{topic}"}

    monkeypatch.setattr(
        "nexus.archive_sync.post_ingest.record_weak_point", fake_record_weak_point
    )

    summary = run_archive_post_ingest(
        archives=archives,
        data_dir=tmp_path / "data",
        db_dir=tmp_path / "chroma",
        sqlite_path=tmp_path / "nexus.db",
        store=_CourseAwareStoreStub(),
    )

    assert summary["status"] == "success"
    assert summary["files_scanned"] == 2
    assert summary["weak_candidates"] == 2
    courses = {course for course, _ in captured_courses}
    assert courses == {"CS101", "MATH201"}
    assert any(course == "CS101" and "pointer" in topic for course, topic in captured_courses)
    assert any(course == "MATH201" and "chain rule" in topic for course, topic in captured_courses)
