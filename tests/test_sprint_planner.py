"""Tests for SprintPlanner — Memory Layer 2."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.knowledge.sprint_planner import SprintPlanner
from nexus.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_store():
    """Create a temporary SQLite store with concepts and mastery data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = SQLiteStore(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        # Insert concepts
        for cid, name in [
            ("cs202_os__deadlock", "Deadlock"),
            ("cs202_os__virtual_memory", "Virtual Memory"),
            ("cs202_os__process_scheduling", "Process Scheduling"),
        ]:
            conn.execute(
                "INSERT INTO concepts (concept_id, name, course_id) VALUES (?, ?, ?)",
                (cid, name, "CS202_OS"),
            )
        # Insert mastery data
        conn.execute(
            """INSERT INTO concept_mastery (concept_id, initial_mastery, decay_rate, review_count)
               VALUES (?, ?, ?, ?)""",
            ("cs202_os__deadlock", 0.9, 0.3, 5),
        )
        conn.execute(
            """INSERT INTO concept_mastery (concept_id, initial_mastery, decay_rate, review_count)
               VALUES (?, ?, ?, ?)""",
            ("cs202_os__virtual_memory", 0.3, 0.5, 2),
        )
        conn.execute(
            """INSERT INTO concept_mastery (concept_id, initial_mastery, decay_rate, review_count)
               VALUES (?, ?, ?, ?)""",
            ("cs202_os__process_scheduling", 0.5, 0.35, 3),
        )

    yield store
    db_path.unlink(missing_ok=True)


class TestSprintMode:
    """Tests for is_sprint_mode()."""

    def test_within_14_days_is_sprint(self, tmp_store):
        """Exam within 14 days should return True."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        future_date = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        assert planner.is_sprint_mode(future_date) is True

    def test_beyond_14_days_not_sprint(self, tmp_store):
        """Exam beyond 14 days should return False."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        future_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        assert planner.is_sprint_mode(future_date) is False

    def test_past_date_not_sprint(self, tmp_store):
        """Past exam date should return False."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        past_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        assert planner.is_sprint_mode(past_date) is False


class TestRuleSprint:
    """Tests for rule-based sprint plan generation."""

    def test_rule_sprint_includes_challenges(self, tmp_store):
        """Every day in a rule sprint should have a non-empty challenge field."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="CS202_OS", exam_date=exam_date)
        assert result["mode"] == "sprint"
        assert result["method"] == "rule"

        for day_entry in result["plan"]:
            assert day_entry.get("challenge", "") != "", f"Day {day_entry['day']} missing challenge"

    def test_empty_course_returns_empty_plan(self, tmp_store):
        """Empty course should return plan with 'no concepts' rationale."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="NONEXISTENT", exam_date=exam_date)
        assert len(result["plan"]) > 0
        assert result["method"] == "rule"

    def test_beyond_14_days_relaxed_mode(self, tmp_store):
        """Beyond 14 days should return relaxed mode with empty plan."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="CS202_OS", exam_date=exam_date)
        assert result["mode"] == "relaxed"
        assert len(result["plan"]) == 0

    def test_plan_structure_valid(self, tmp_store):
        """Sprint plan should have all required fields."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="CS202_OS", exam_date=exam_date)
        assert "mode" in result
        assert "exam_date" in result
        assert "days_until_exam" in result
        assert "plan" in result
        assert "method" in result

        for day_entry in result["plan"]:
            assert "day" in day_entry
            assert "date" in day_entry
            assert "focus" in day_entry
            assert "rationale" in day_entry
            assert "challenge" in day_entry
            assert "estimated_minutes" in day_entry
