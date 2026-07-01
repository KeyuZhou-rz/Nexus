"""X1-X3: Tests for LMS base interface, Brightspace connector, Canvas connector."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nexus.aggregators.lms_base import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    LMSConnector,
    _format_bytes,
)
from nexus.aggregators.brightspace import FeedConfig


# ── X1: Domain models & LMSConnector ─────────────────────────────────

class TestDomainModels:
    def test_course_creation(self):
        c = Course(course_id="CS101", name="Intro to CS", term="Fall 2026")
        assert c.course_id == "CS101"
        assert c.name == "Intro to CS"

    def test_assignment_creation(self):
        due = datetime(2026, 7, 15, 12, 0)
        a = Assignment(
            assignment_id="a1", title="HW1", course_id="CS101",
            due_at=due, points_possible=100.0,
        )
        assert a.assignment_id == "a1"
        assert a.due_at == due
        assert a.points_possible == 100.0

    def test_announcement_creation(self):
        ann = Announcement(
            announcement_id="n1", title="Welcome", course_id="CS101",
            body="Hello class!", posted_at=datetime.now(),
        )
        assert ann.announcement_id == "n1"
        assert ann.body == "Hello class!"

    def test_course_file_creation(self):
        f = CourseFile(
            file_id="f1", name="lecture.pdf", course_id="CS101",
            content_type="application/pdf", size_bytes=1024,
        )
        assert f.file_id == "f1"
        assert f.size_bytes == 1024

    def test_assignment_defaults(self):
        a = Assignment(assignment_id="a1", title="HW1", course_id="CS101")
        assert a.due_at is None
        assert a.url is None
        assert a.description is None
        assert a.meta == {}


class TestFormatBytes:
    def test_none(self):
        assert "unknown" in _format_bytes(None)

    def test_bytes(self):
        assert _format_bytes(500) == "500 B"

    def test_kilobytes(self):
        assert "KB" in _format_bytes(2048)

    def test_megabytes(self):
        result = _format_bytes(3 * 1024 * 1024)
        assert "MB" in result


class _FakeLMS(LMSConnector):
    """Minimal LMSConnector implementation for testing to_tasks()."""
    name = "fake"

    def list_courses(self):
        return [Course(course_id="CS101", name="Intro")]

    def list_assignments(self, course_id):
        return [
            Assignment("a1", "HW1", "CS101", due_at=datetime(2026, 7, 15)),
            Assignment("a2", "Midterm Exam", "CS101",
                       due_at=datetime(2026, 8, 1),
                       description="Chapters 1-5"),
        ]

    def list_announcements(self, course_id):
        return [
            Announcement("n1", "Welcome", "CS101",
                         body="Welcome to CS101", posted_at=datetime.now()),
        ]

    def list_files(self, course_id):
        return [
            CourseFile("f1", "syllabus.pdf", "CS101",
                       content_type="application/pdf", size_bytes=2048),
        ]


class TestLMSConnectorToTasks:
    def test_to_tasks_all_courses(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks()
        assert len(tasks) == 4  # 2 assignments + 1 announcement + 1 file

    def test_to_tasks_single_course(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks(course_id="CS101")
        assert len(tasks) == 4

    def test_tasks_have_correct_sources(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks()
        sources = {t.source for t in tasks}
        assert "fake" in sources

    def test_assignment_tasks_have_due_dates(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks()
        assignments = [t for t in tasks if "assignment" in t.tags]
        assert len(assignments) == 2
        assert any(t.due_at is not None for t in assignments)

    def test_announcement_tasks_have_no_due_date(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks()
        announcements = [t for t in tasks if "announcement" in t.tags]
        assert len(announcements) == 1
        assert announcements[0].due_at is None

    def test_exam_title_adds_exam_tag(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks()
        exam_tasks = [t for t in tasks if "exam" in t.tags]
        assert len(exam_tasks) == 1
        assert "Midterm Exam" in exam_tasks[0].title

    def test_file_task_has_size_in_snippet(self):
        connector = _FakeLMS()
        tasks = connector.to_tasks()
        file_tasks = [t for t in tasks if "resource" in t.tags]
        assert len(file_tasks) == 1
        assert "2 KB" in file_tasks[0].snippet


# ── X2: BrightspaceLMSConnector ──────────────────────────────────────

class TestBrightspaceLMSConnector:
    def test_list_courses_from_feeds(self):
        from nexus.aggregators.brightspace_lms import BrightspaceLMSConnector

        connector = BrightspaceLMSConnector(
            ical_feeds=[
                FeedConfig(name="OS", url="http://example.com/os.ics", course="CS202"),
                FeedConfig(name="DB", url="http://example.com/db.ics", course="CS305"),
            ],
        )
        courses = connector.list_courses()
        assert len(courses) == 2
        course_ids = {c.course_id for c in courses}
        assert course_ids == {"CS202", "CS305"}

    def test_list_courses_dedupes(self):
        from nexus.aggregators.brightspace_lms import BrightspaceLMSConnector

        connector = BrightspaceLMSConnector(
            ical_feeds=[FeedConfig(name="OS", url="http://ex.com/os.ics", course="CS202")],
            rss_feeds=[FeedConfig(name="OS", url="http://ex.com/os.rss", course="CS202")],
        )
        courses = connector.list_courses()
        assert len(courses) == 1  # deduped

    def test_list_assignments_filters_by_course(self):
        from nexus.aggregators.brightspace_lms import BrightspaceLMSConnector

        connector = BrightspaceLMSConnector(
            ical_feeds=[FeedConfig(name="DB", url="http://ex.com/db.ics", course="CS305")],
        )
        # No feed for CS202 → empty
        assignments = connector.list_assignments("CS202")
        assert assignments == []

    def test_empty_feeds_return_empty_lists(self):
        from nexus.aggregators.brightspace_lms import BrightspaceLMSConnector

        connector = BrightspaceLMSConnector()
        assert connector.list_courses() == []
        assert connector.list_assignments("ANY") == []
        assert connector.list_announcements("ANY") == []
        assert connector.list_files("ANY") == []

    def test_to_tasks_integration(self, monkeypatch):
        from nexus.aggregators.brightspace_lms import BrightspaceLMSConnector
        from icalendar import Calendar

        connector = BrightspaceLMSConnector(
            ical_feeds=[
                FeedConfig(name="Test", url="http://ex.com/test.ics", course="CS999"),
            ],
        )
        # Mock _load_ical to return an empty calendar (avoid real HTTP call)
        monkeypatch.setattr(
            connector, "_load_ical", lambda feed: Calendar()
        )
        tasks = connector.to_tasks()
        # Empty iCal → no assignments → to_tasks returns 0 tasks for empty course list
        assert tasks == []

    def test_list_files_no_archive_dir(self):
        from nexus.aggregators.brightspace_lms import BrightspaceLMSConnector

        connector = BrightspaceLMSConnector()
        assert connector.list_files("CS202") == []


# ── X3: CanvasLMSConnector ──────────────────────────────────────────

class TestCanvasLMSConnector:
    def test_initialization(self):
        from nexus.aggregators.canvas import CanvasLMSConnector

        connector = CanvasLMSConnector(
            base_url="https://canvas.instructure.com",
            token="test-token-123",
        )
        assert connector.name == "canvas"
        assert connector.base_url == "https://canvas.instructure.com"
        assert connector.token == "test-token-123"
        assert connector.per_page == 50

    def test_per_page_capped_at_100(self):
        from nexus.aggregators.canvas import CanvasLMSConnector

        connector = CanvasLMSConnector(
            base_url="https://canvas.example.com",
            token="x",
            per_page=200,
        )
        assert connector.per_page == 100

    def test_no_token_returns_empty_lists(self):
        from nexus.aggregators.canvas import CanvasLMSConnector

        connector = CanvasLMSConnector(
            base_url="https://canvas.example.com",
            token=None,
        )
        # Without a real HTTP call, _paginate returns [] when token is None
        assert connector.list_courses() == []
        assert connector.list_assignments("1") == []

    def test_headers_contain_bearer(self):
        from nexus.aggregators.canvas import CanvasLMSConnector

        connector = CanvasLMSConnector(
            base_url="https://canvas.example.com",
            token="my-token",
        )
        headers = connector._headers()
        assert headers["Authorization"] == "Bearer my-token"
        assert headers["Accept"] == "application/json"

    def test_to_tasks_without_token(self):
        from nexus.aggregators.canvas import CanvasLMSConnector

        connector = CanvasLMSConnector(
            base_url="https://canvas.example.com",
            token=None,
        )
        tasks = connector.to_tasks()
        assert tasks == []  # no courses → no tasks
