from __future__ import annotations

from datetime import datetime

from nexus.archive_sync.archive_utils import (
    build_archive_filename,
    is_supported_attachment,
    sanitize_segment,
)


def test_supported_attachment_detection():
    assert is_supported_attachment("https://a/b/c.pdf")
    assert is_supported_attachment("https://a/b/c", "notes.docx")
    assert not is_supported_attachment("https://a/b/c", "noext")


def test_sanitize_segment():
    assert sanitize_segment("General Physics II") == "General_Physics_II"
    assert sanitize_segment("  ###  ") == "unknown"


def test_build_archive_filename_includes_due_and_ext():
    name = build_archive_filename(
        course="General Physics II",
        title="HW3 Kinematics",
        due_at=datetime(2026, 2, 28),
        source_url="https://example.com/hw3.pdf",
    )
    assert name.startswith("General_Physics_II_HW3_Kinematics_20260228_")
    assert name.endswith(".pdf")


def test_session_save_and_load_respect_nexus_data_dir(tmp_path, monkeypatch):
    from nexus.archive_sync.session import load_session, save_session

    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    cookies = [{"name": "session", "value": "abc123"}]
    save_session(cookies)

    expected_path = tmp_path / "brightspace_session.json"
    assert expected_path.exists()
    assert load_session() == cookies


def test_session_explicit_path_overrides_env_var(tmp_path, monkeypatch):
    from nexus.archive_sync.session import load_session, save_session

    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path / "env_dir"))
    explicit = tmp_path / "explicit" / "session.json"
    cookies = [{"name": "explicit", "value": "xyz"}]
    save_session(cookies, path=explicit)

    assert explicit.exists()
    assert load_session(path=explicit) == cookies
    assert not (tmp_path / "env_dir" / "brightspace_session.json").exists()
