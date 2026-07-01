"""Tests for ExamRetrospective — Memory Layer 3."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.knowledge.retrospective import ExamRetrospective
from nexus.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_store():
    """Create a temporary SQLite store with mastery data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = SQLiteStore(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        # Insert concepts
        for cid, name in [
            ("cs202_os__deadlock", "Deadlock"),
            ("cs202_os__virtual_memory", "Virtual Memory"),
        ]:
            conn.execute(
                "INSERT INTO concepts (concept_id, name, course_id) VALUES (?, ?, ?)",
                (cid, name, "CS202_OS"),
            )
        # Insert mastery data (one critical, one mastered)
        conn.execute(
            """INSERT INTO concept_mastery (concept_id, initial_mastery, decay_rate, review_count, last_reviewed)
               VALUES (?, ?, ?, ?, ?)""",
            ("cs202_os__deadlock", 0.9, 0.3, 5, "2026-07-01T00:00:00Z"),
        )
        conn.execute(
            """INSERT INTO concept_mastery (concept_id, initial_mastery, decay_rate, review_count, last_reviewed)
               VALUES (?, ?, ?, ?, ?)""",
            ("cs202_os__virtual_memory", 0.2, 0.5, 2, "2026-06-20T00:00:00Z"),
        )

    yield store
    db_path.unlink(missing_ok=True)


class TestRecordExam:
    """Tests for record_exam()."""

    def test_record_exam_creates_record(self, tmp_store):
        """record_exam should create an exam record."""
        retro = ExamRetrospective(tmp_store)
        exam_id = retro.record_exam("CS202_OS", "2026-07-15")

        assert exam_id == "CS202_OS__2026-07-15"

        record = tmp_store.get_exam_record(exam_id)
        assert record is not None
        assert record["course_id"] == "CS202_OS"
        assert record["exam_date"] == "2026-07-15"

    def test_record_exam_with_user_input(self, tmp_store):
        """record_exam should store user_input."""
        retro = ExamRetrospective(tmp_store)
        exam_id = retro.record_exam(
            "CS202_OS", "2026-07-15",
            user_input="死锁考了大题，虚拟内存完全没考",
        )

        record = tmp_store.get_exam_record(exam_id)
        assert "死锁考了大题" in record["user_input"]


class TestGenerateRetrospective:
    """Tests for generate_retrospective()."""

    def test_generate_with_mocked_llm(self, tmp_store):
        """generate_retrospective should persist LLM analysis."""
        retro = ExamRetrospective(tmp_store)
        exam_id = retro.record_exam("CS202_OS", "2026-07-15")

        fake_response = MagicMock()
        fake_response.content = json.dumps({
            "predicted_weak": ["Virtual Memory"],
            "actual_hits": ["Deadlock"],
            "blind_spots": ["Process Scheduling"],
            "overestimated": ["Virtual Memory"],
            "analysis_text": "你考前担心的Virtual Memory并没有考，但Deadlock确实出现了。盲点Process Scheduling需要重视。",
            "retention_hook": "死锁四条件：互斥占有不剥夺，循环等待——'互占不夺循环等'",
        })

        with patch("nexus.knowledge.llm_gateway.gateway.chat", return_value=fake_response):
            result = retro.generate_retrospective(exam_id, "死锁考了大题")

        assert result["course_id"] == "CS202_OS"
        assert "Deadlock" in result["actual_hits"]
        assert "Virtual Memory" in result["predicted_weak"]
        assert "Process Scheduling" in result["blind_spots"]
        assert "Virtual Memory" in result["overestimated"]
        assert len(result["analysis_text"]) > 0
        assert len(result["retention_hook"]) > 0
        assert result["hit_rate"] == "1/1"


class TestListExams:
    """Tests for list_exams()."""

    def test_list_exams_returns_all(self, tmp_store):
        """list_exams should return all exams for a course."""
        retro = ExamRetrospective(tmp_store)
        retro.record_exam("CS202_OS", "2026-07-15")
        retro.record_exam("CS202_OS", "2026-06-30")

        exams = retro.list_exams("CS202_OS")
        assert len(exams) == 2
        dates = [e["exam_date"] for e in exams]
        assert "2026-07-15" in dates
        assert "2026-06-30" in dates
