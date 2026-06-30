"""Unit tests for briefing.py pure functions — no network / LLM / DashScope."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nexus.intelligence.briefing import (
    _contains_cjk,
    _friendly_text,
    _has_action_keywords,
    _is_noise,
    _normalize_title,
    _rule_briefing,
    select_exam_reminders,
)
from nexus.models import Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    id: str = "t1",
    title: str = "Some task",
    due_at: datetime | None = None,
    source: str = "gmail",
    tags: list[str] | None = None,
    snippet: str | None = None,
    course: str | None = None,
    url: str | None = None,
) -> Task:
    return Task(
        id=id,
        title=title,
        due_at=due_at,
        source=source,
        tags=tags or [],
        snippet=snippet,
        course=course,
        url=url,
    )


# ===================================================================
# select_exam_reminders
# ===================================================================

class TestSelectExamReminders:
    def test_select_exam_reminders_within_window(self):
        """Exam due 30 days from now is selected."""
        due = datetime.now() + timedelta(days=30)
        task = _make_task(id="e1", title="Final Exam", due_at=due, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == [task]

    def test_select_exam_reminders_outside_window_excluded(self):
        """Exam due 200 days from now is excluded when window=180."""
        due = datetime.now() + timedelta(days=200)
        task = _make_task(id="e2", title="Final Exam", due_at=due, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == []

    def test_select_exam_reminders_no_exam_tag_excluded(self):
        """Task due in 30 days but without 'exam' tag is excluded."""
        due = datetime.now() + timedelta(days=30)
        task = _make_task(id="e3", title="Homework", due_at=due, tags=["homework"])
        result = select_exam_reminders([task], window_days=180)
        assert result == []

    def test_select_exam_reminders_due_at_none_excluded(self):
        """Task with 'exam' tag but due_at=None is excluded."""
        task = _make_task(id="e4", title="Exam TBA", due_at=None, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == []

    def test_select_exam_reminders_past_due_excluded(self):
        """Exam due yesterday is excluded."""
        due = datetime.now() - timedelta(days=1)
        task = _make_task(id="e5", title="Past Exam", due_at=due, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == []

    def test_select_exam_reminders_tz_aware_within_window(self):
        """Exam with tzinfo due 30 days from now is selected."""
        tz = timezone(timedelta(hours=8))
        due = datetime.now(tz=tz) + timedelta(days=30)
        task = _make_task(id="e6", title="TZ Exam", due_at=due, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == [task]

    def test_select_exam_reminders_tz_aware_outside_window_excluded(self):
        """TZ-aware exam due 200 days from now is excluded when window=180."""
        tz = timezone(timedelta(hours=8))
        due = datetime.now(tz=tz) + timedelta(days=200)
        task = _make_task(id="e7", title="Far Future TZ Exam", due_at=due, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == []

    def test_select_exam_reminders_tz_aware_past_due_excluded(self):
        """TZ-aware exam due yesterday is excluded."""
        tz = timezone(timedelta(hours=8))
        due = datetime.now(tz=tz) - timedelta(days=1)
        task = _make_task(id="e8", title="Past TZ Exam", due_at=due, tags=["exam"])
        result = select_exam_reminders([task], window_days=180)
        assert result == []


# ===================================================================
# _is_noise
# ===================================================================

class TestIsNoise:
    def test_is_noise_true_announcement_no_keyword(self):
        """Announcement tag + no action keyword => noise."""
        task = _make_task(
            id="n1", title="Weekly digest", tags=["announcement"], snippet="Nothing urgent",
        )
        assert _is_noise(task) is True

    def test_is_noise_false_announcement_with_keyword(self):
        """Announcement tag but title contains 'exam' => not noise."""
        task = _make_task(
            id="n2", title="Midterm exam schedule", tags=["announcement"],
        )
        assert _is_noise(task) is False

    def test_is_noise_false_not_announcement(self):
        """No announcement tag and no keyword => not noise."""
        task = _make_task(
            id="n3", title="Weekly digest", tags=["info"],
        )
        assert _is_noise(task) is False


# ===================================================================
# _has_action_keywords
# ===================================================================

class TestHasActionKeywords:
    def test_has_action_keywords_english_lowercase(self):
        assert _has_action_keywords("please submit your assignment") is True

    def test_has_action_keywords_english_uppercase(self):
        assert _has_action_keywords("PLEASE SUBMIT YOUR ASSIGNMENT") is True

    def test_has_action_keywords_chinese(self):
        assert _has_action_keywords("请截止前提交") is True

    def test_has_action_keywords_none(self):
        assert _has_action_keywords("hello world") is False


# ===================================================================
# _rule_briefing
# ===================================================================

class TestRuleBriefing:
    def test_rule_briefing_schedule_vs_todo_split(self):
        """Task with due_at goes to schedule; without goes to todo."""
        now = datetime.now().astimezone()
        due = now + timedelta(days=1)
        t_sched = _make_task(id="s1", title="Midterm", due_at=due, source="brightspace")
        t_todo = _make_task(id="d1", title="Check email", due_at=None, source="gmail", tags=["announcement"])
        task_index = {t.id: t for t in [t_sched, t_todo]}
        briefing = _rule_briefing([t_sched, t_todo], task_index, now)
        assert len(briefing.schedule) == 1
        assert len(briefing.todo) == 1
        assert briefing.schedule[0].source_ids == ["s1"]
        assert briefing.todo[0].source_ids == ["d1"]
        assert "Midterm" in briefing.schedule[0].text_en

    def test_rule_briefing_schedule_sorted_by_due_at(self):
        """Schedule items sorted by due_at ascending."""
        now = datetime.now().astimezone()
        d1 = now + timedelta(days=3)
        d2 = now + timedelta(days=1)
        d3 = now + timedelta(days=2)
        t1 = _make_task(id="a", title="Late", due_at=d1)
        t2 = _make_task(id="b", title="Early", due_at=d2)
        t3 = _make_task(id="c", title="Mid", due_at=d3)
        task_index = {t.id: t for t in [t1, t2, t3]}
        briefing = _rule_briefing([t1, t2, t3], task_index, now)
        assert [item.source_ids[0] for item in briefing.schedule] == ["b", "c", "a"]


# ===================================================================
# _normalize_title
# ===================================================================

class TestNormalizeTitle:
    def test_normalize_title_appends_course(self):
        task = _make_task(id="x", title="Midterm", course="CS202")
        assert _normalize_title(task) == "Midterm (CS202)"

    def test_normalize_title_course_already_present(self):
        task = _make_task(id="x", title="CS202 Midterm", course="CS202")
        assert _normalize_title(task) == "CS202 Midterm"


# ===================================================================
# _contains_cjk
# ===================================================================

class TestContainsCjk:
    def test_contains_cjk_mixed(self):
        assert _contains_cjk("hello 你好") is True

    def test_contains_cjk_ascii_only(self):
        assert _contains_cjk("hello") is False

    def test_contains_cjk_empty(self):
        assert _contains_cjk("") is False


# ===================================================================
# _friendly_text
# ===================================================================

class TestFriendlyText:
    def test_friendly_text_overdue(self):
        """Task due 1 hour ago => 'Overdue: ...'."""
        now = datetime.now().astimezone()
        due = now - timedelta(hours=1)
        task = _make_task(id="o1", title="Late Paper", due_at=due)
        text_en, text_zh = _friendly_text(task, "Late Paper", now)
        assert text_en.startswith("Overdue:")
        assert text_zh.startswith("已过期：")

    def test_friendly_text_top_priority(self):
        """Task due in 12 hours => 'Top priority: ...'."""
        now = datetime.now().astimezone()
        due = now + timedelta(hours=12)
        task = _make_task(id="u1", title="Urgent HW", due_at=due)
        text_en, text_zh = _friendly_text(task, "Urgent HW", now)
        assert text_en.startswith("Top priority:")
        assert text_zh.startswith("今天优先：")

    def test_friendly_text_due_soon(self):
        """Task due in 1.5 days => 'Due soon: ...'."""
        now = datetime.now().astimezone()
        due = now + timedelta(hours=36)
        task = _make_task(id="ds1", title="Upcoming", due_at=due)
        text_en, text_zh = _friendly_text(task, "Upcoming", now)
        assert text_en.startswith("Due soon:")
        assert text_zh.startswith("尽快安排：")

    def test_friendly_text_on_schedule(self):
        """Task due in 5 days => 'On the schedule: ...'."""
        now = datetime.now().astimezone()
        due = now + timedelta(days=5)
        task = _make_task(id="os1", title="Future", due_at=due)
        text_en, text_zh = _friendly_text(task, "Future", now)
        assert text_en.startswith("On the schedule:")
        assert text_zh.startswith("日程安排：")

    def test_friendly_text_no_due_gmail_announcement(self):
        """Gmail + announcement tag + no due => 'FYI: ...'."""
        now = datetime.now().astimezone()
        task = _make_task(
            id="fyi1", title="Newsletter", due_at=None,
            source="gmail", tags=["announcement"],
        )
        text_en, text_zh = _friendly_text(task, "Newsletter", now)
        assert text_en.startswith("FYI:")
        assert text_zh.startswith("通知：")

    def test_friendly_text_no_due_gmail_course_notification(self):
        now = datetime.now().astimezone()
        task = _make_task(
            id="cn1", title="Grade posted", due_at=None,
            source="gmail", tags=["course_notification"],
        )
        text_en, text_zh = _friendly_text(task, "Grade posted", now)
        assert text_en.startswith("Course update:")
        assert text_zh.startswith("课程提醒：")

    def test_friendly_text_no_due_gmail_other(self):
        now = datetime.now().astimezone()
        task = _make_task(
            id="gm1", title="Random email", due_at=None,
            source="gmail", tags=[],
        )
        text_en, text_zh = _friendly_text(task, "Random email", now)
        assert text_en.startswith("Mail to check:")
        assert text_zh.startswith("邮件提醒：")

    def test_friendly_text_no_due_brightspace(self):
        now = datetime.now().astimezone()
        task = _make_task(
            id="bs1", title="New module", due_at=None, source="brightspace",
        )
        text_en, text_zh = _friendly_text(task, "New module", now)
        assert text_en.startswith("Course update:")
        assert text_zh.startswith("课程更新：")

    def test_friendly_text_no_due_gcal(self):
        now = datetime.now().astimezone()
        task = _make_task(
            id="gc1", title="Team sync", due_at=None, source="gcal",
        )
        text_en, text_zh = _friendly_text(task, "Team sync", now)
        assert text_en.startswith("Calendar:")
        assert text_zh.startswith("日程：")

    def test_friendly_text_no_due_unknown_source(self):
        now = datetime.now().astimezone()
        task = _make_task(
            id="unk1", title="Mystery", due_at=None, source="slack",
        )
        text_en, text_zh = _friendly_text(task, "Mystery", now)
        assert text_en.startswith("To review:")
        assert text_zh.startswith("待处理：")
