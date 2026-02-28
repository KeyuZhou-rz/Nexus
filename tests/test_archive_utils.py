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
