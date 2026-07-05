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
from nexus.knowledge.temporal_memory import TemporalMemory


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


@pytest.fixture
def hit_rate_store(tmp_path):
    """
    A store dedicated to hit_rate tests, where mastery is set via
    TemporalMemory.record_event() *at fixture-creation time* instead of
    hardcoded past ISO timestamps — so which concept lands in "predicted weak"
    doesn't drift with wall-clock decay between when the fixture was written
    and when the test actually runs (last_reviewed ≈ now for both concepts).
    """
    db_path = tmp_path / "retro_hit_rate.db"
    store = SQLiteStore(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for cid, name in [
            ("cs202_os__deadlock", "Deadlock"),
            ("cs202_os__virtual_memory", "Virtual Memory"),
        ]:
            conn.execute(
                "INSERT INTO concepts (concept_id, name, course_id) VALUES (?, ?, ?)",
                (cid, name, "CS202_OS"),
            )

    temporal = TemporalMemory(store)
    for _ in range(6):
        temporal.record_event("cs202_os__deadlock", "correct_answer")  # -> mastered
    temporal.record_event("cs202_os__virtual_memory", "struggled")  # -> critical

    return store


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

    def test_generate_with_mocked_llm(self, hit_rate_store):
        """generate_retrospective should persist LLM analysis, with predicted_weak
        coming from the system's own pre-exam TemporalMemory snapshot (Deadlock
        was reinforced to "mastered", so only Virtual Memory is predicted weak)."""
        retro = ExamRetrospective(hit_rate_store)
        exam_id = retro.record_exam("CS202_OS", "2026-07-15")

        fake_response = MagicMock()
        fake_response.content = json.dumps({
            "actual_hits": ["Virtual Memory"],
            "blind_spots": ["Process Scheduling"],
            "overestimated": [],
            "analysis_text": "你考前担心的Virtual Memory确实考到了，说明预测准确。",
            "retention_hook": "虚拟内存三件套：分页分段加TLB",
        })

        with patch("nexus.knowledge.llm_gateway.gateway.chat", return_value=fake_response):
            result = retro.generate_retrospective(exam_id, "虚拟内存考了大题")

        assert result["course_id"] == "CS202_OS"
        # predicted_weak is system-computed, not echoed back by the LLM
        assert result["predicted_weak"] == ["Virtual Memory"]
        assert result["actual_hits"] == ["Virtual Memory"]
        assert "Process Scheduling" in result["blind_spots"]
        assert len(result["analysis_text"]) > 0
        assert len(result["retention_hook"]) > 0
        assert result["hit_rate"] == "1/1"

    def test_hallucinated_hit_outside_prediction_does_not_inflate_hit_rate(self, hit_rate_store):
        """If the LLM claims a 'hit' on a concept that was never in the system's
        real pre-exam prediction, it must be filtered out rather than counted —
        this is the fix for the old self-eval bug (predicted/actual both LLM-made
        in the same call, so a hallucinated hit used to count as 1/1 accuracy)."""
        retro = ExamRetrospective(hit_rate_store)
        exam_id = retro.record_exam("CS202_OS", "2026-07-15")

        fake_response = MagicMock()
        fake_response.content = json.dumps({
            # Deadlock was reinforced to "mastered" in the fixture — it was
            # never actually predicted weak, so this "hit" is a hallucination.
            "actual_hits": ["Deadlock"],
            "blind_spots": [],
            "overestimated": [],
            "analysis_text": "占位分析文本。",
            "retention_hook": "",
        })

        with patch("nexus.knowledge.llm_gateway.gateway.chat", return_value=fake_response):
            result = retro.generate_retrospective(exam_id, "死锁考了大题")

        assert result["predicted_weak"] == ["Virtual Memory"]
        assert result["actual_hits"] == []
        assert result["hit_rate"] == "0/1"


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
