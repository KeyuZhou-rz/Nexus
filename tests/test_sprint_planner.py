"""Tests for SprintPlanner — Memory Layer 2."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.knowledge.sprint_planner import SprintPlanner, _SprintConcept
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
            chunk_id = f"chunk__{cid}"
            conn.execute(
                "INSERT INTO chunks (chunk_id, document_id, course_id, topic) VALUES (?, ?, ?, ?)",
                (chunk_id, "doc__cs202", "CS202_OS", name),
            )
            conn.execute(
                "INSERT INTO chunk_concepts (chunk_id, concept_id) VALUES (?, ?)",
                (chunk_id, cid),
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

        result = planner.generate(course_id="CS202_OS", exam_date=exam_date, chapter_weights={"ch1": 1.0})
        assert result["mode"] == "sprint"
        assert result["method"] == "rule"

        for day_entry in result["plan"]:
            assert day_entry.get("challenge", "") != "", f"Day {day_entry['day']} missing challenge"

    def test_empty_course_returns_empty_plan(self, tmp_store):
        """Empty course should return plan with 'no concepts' rationale."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="NONEXISTENT", exam_date=exam_date, chapter_weights={"ch1": 1.0})
        assert len(result["plan"]) > 0
        assert result["method"] == "rule"

    def test_beyond_14_days_relaxed_mode(self, tmp_store):
        """Beyond 14 days should return relaxed mode with empty plan."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="CS202_OS", exam_date=exam_date, chapter_weights={"ch1": 1.0})
        assert result["mode"] == "relaxed"
        assert len(result["plan"]) == 0

    def test_plan_structure_valid(self, tmp_store):
        """Sprint plan should have all required fields."""
        from datetime import datetime, timedelta
        planner = SprintPlanner(tmp_store)
        exam_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        result = planner.generate(course_id="CS202_OS", exam_date=exam_date, chapter_weights={"ch1": 1.0})
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

    def test_requires_explicit_chapter_weights(self, tmp_store):
        from datetime import datetime, timedelta
        exam_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        result = SprintPlanner(tmp_store).generate("CS202_OS", exam_date)
        assert result["mode"] == "unavailable"
        assert result["plan"] == []


def _concept(mastery: float, name: str = "X") -> _SprintConcept:
    return _SprintConcept(
        concept_id=f"c__{name}", name=name, mastery=mastery,
        prereq_depth=0, review_count=0,
    )


class TestEstimateMinutes:
    """Regression tests: estimated_minutes used to be a flat
    daily_hours*60*0.75 regardless of how many concepts were assigned that
    day or how weak they were."""

    def test_no_concepts_falls_back_to_half_available_time(self):
        minutes = SprintPlanner._estimate_minutes([], daily_hours=2.0)
        assert minutes == int(2.0 * 60 * 0.5)

    def test_scales_with_concept_count(self):
        """More concepts assigned the same day -> more estimated time."""
        one = SprintPlanner._estimate_minutes([(1.0, _concept(0.5))], daily_hours=4.0)
        three = SprintPlanner._estimate_minutes(
            [(1.0, _concept(0.5)), (1.0, _concept(0.5)), (1.0, _concept(0.5))],
            daily_hours=4.0,
        )
        assert three > one

    def test_scales_with_mastery_gap(self):
        """Weaker concepts (larger mastery gap) -> more estimated time than
        near-mastered ones, for the same concept count."""
        weak = SprintPlanner._estimate_minutes([(1.0, _concept(0.05))], daily_hours=4.0)
        strong = SprintPlanner._estimate_minutes([(1.0, _concept(0.95))], daily_hours=4.0)
        assert weak > strong

    def test_capped_at_available_daily_time(self):
        """Even with many weak concepts, estimate never exceeds daily_hours*60."""
        many_weak = [(1.0, _concept(0.0, name=f"c{i}")) for i in range(20)]
        minutes = SprintPlanner._estimate_minutes(many_weak, daily_hours=0.5)
        assert minutes <= int(0.5 * 60)

    def test_floored_at_20_minutes(self):
        """A single near-mastered concept shouldn't round down to something
        silly like 11 minutes."""
        minutes = SprintPlanner._estimate_minutes([(1.0, _concept(0.9))], daily_hours=4.0)
        assert minutes >= 20


class TestGuessChapter:
    """Regression tests: _guess_chapter only matched ch/chapter patterns,
    ignoring common Lecture/Unit/Week naming conventions in concept
    topic/name text (lecture_number DB field was already a separate,
    working fallback)."""

    def test_matches_chapter_pattern(self):
        assert SprintPlanner._guess_chapter({"name": "Chapter 3: Deadlock"}) == "ch3"

    def test_matches_ch_abbreviation(self):
        assert SprintPlanner._guess_chapter({"name": "ch7 Virtual Memory"}) == "ch7"

    def test_matches_lecture_in_text(self):
        assert SprintPlanner._guess_chapter({"name": "Lecture 5: Paging"}) == "ch5"

    def test_matches_lec_abbreviation(self):
        assert SprintPlanner._guess_chapter({"topic": "lec 9 review"}) == "ch9"

    def test_matches_unit_in_text(self):
        assert SprintPlanner._guess_chapter({"name": "Unit 2 basics"}) == "ch2"

    def test_matches_week_in_text(self):
        assert SprintPlanner._guess_chapter({"topic": "Week 3 review session"}) == "ch3"

    def test_falls_back_to_lecture_number_field(self):
        assert SprintPlanner._guess_chapter({"name": "Something unrelated", "lecture_number": 4}) == "ch4"

    def test_no_match_returns_none(self):
        assert SprintPlanner._guess_chapter({"name": "Something unrelated"}) is None
