from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nexus.dashboard.view_model import (
    DashboardItem,
    _briefing_lines,
    _greeting,
    _localize,
    _pipeline_summary,
    _relative_update_text,
    _when_text,
    build_dashboard_state,
    render_task_card,
)
from nexus.models import Task


def _task(
    *,
    title: str,
    due_at: datetime | None = None,
    source: str = "brightspace",
    course: str | None = "CS202_OS",
    tags: list[str] | None = None,
    received_at: datetime | None = None,
) -> Task:
    return Task(
        id=title,
        title=title,
        due_at=due_at,
        source=source,
        course=course,
        tags=tags or [],
        received_at=received_at,
    )


def test_build_dashboard_state_prioritizes_overdue_and_today():
    now = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
    tasks = [
        _task(title="Later task", due_at=now + timedelta(days=10), tags=["assignment"]),
        _task(title="Today task", due_at=now + timedelta(hours=3), tags=["assignment"]),
        _task(title="Overdue task", due_at=now - timedelta(hours=2), tags=["assignment"]),
    ]

    state = build_dashboard_state(tasks, generated_at=now)

    assert state.snapshot.overdue == 1
    assert state.snapshot.due_today == 2
    assert [item.title for item in state.focus_items[:2]] == ["Overdue task", "Today task"]
    assert [group.label for group in state.upcoming_groups][:2] == ["Overdue", "Today"]


def test_build_dashboard_state_collects_recent_updates_and_briefing():
    now = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
    tasks = [
        _task(
            title="Announcement",
            source="brightspace",
            tags=["announcement"],
            received_at=now - timedelta(hours=1),
        ),
        _task(
            title="[Grade] Homework 1",
            source="gmail",
            tags=["grade"],
            received_at=now - timedelta(hours=2),
        ),
    ]
    briefing = {
        "todo": [{"text_en": "Submit lab report"}],
        "schedule": [{"text_en": "Attend office hour"}],
        "warnings": ["LLM unavailable"],
    }
    report = {
        "ok": False,
        "steps": [
            {"name": "aggregation", "ok": False, "message": "Google auth missing"},
            {"name": "briefing", "ok": True, "message": "briefing generated"},
        ],
    }

    state = build_dashboard_state(
        tasks,
        generated_at=now,
        briefing_payload=briefing,
        pipeline_report=report,
    )

    assert state.snapshot.recent_updates == 2
    assert [item.title for item in state.update_items] == ["Announcement", "[Grade] Homework 1"]
    assert state.briefing_lines == ["Submit lab report", "Attend office hour"]
    assert state.pipeline_ok is False
    assert "aggregation" in state.pipeline_summary


# ---------------------------------------------------------------------------
# 1. Empty task list
# ---------------------------------------------------------------------------

def test_build_empty_no_exception():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    state = build_dashboard_state([], generated_at=now)
    assert state.snapshot.total_tasks == 0
    assert state.snapshot.overdue == 0
    assert state.snapshot.due_today == 0
    assert state.snapshot.next_seven_days == 0
    assert state.snapshot.recent_updates == 0
    assert state.focus_items == []
    assert state.upcoming_groups == []
    assert state.tasks_table == []
    assert state.available_courses == []
    assert state.available_sources == []
    assert state.available_tags == []


# ---------------------------------------------------------------------------
# 2. Task with missing fields (no due_at, no received_at, no course, no source, no tags)
# ---------------------------------------------------------------------------

def test_missing_fields_no_crash():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="t1", title="bare task", due_at=None, source="", course=None, tags=[])
    state = build_dashboard_state([t], generated_at=now)
    # No due_at -> not actionable -> focus_items empty
    assert state.focus_items == []
    # _when_text directly: no due_at and no received_at -> "No time"
    assert _when_text(t, now) == "No time"
    # tasks_table row: course and tags should be empty string
    row = state.tasks_table[0]
    assert row["course"] == ""
    assert row["tags"] == ""
    assert row["when"] == "No time"


# ---------------------------------------------------------------------------
# 3. Task title starts with "[Grade]" but no grade tag -> is_update_task
# ---------------------------------------------------------------------------

def test_is_update_task_title_grade():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="t3", title="[Grade] x", due_at=None, source="brightspace", tags=[])
    state = build_dashboard_state([t], generated_at=now)
    assert any(item.title == "[Grade] x" for item in state.update_items)


# ---------------------------------------------------------------------------
# 4. briefing_payload empty dict -> empty lines and warnings
# ---------------------------------------------------------------------------

def test_briefing_empty_payload():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    state = build_dashboard_state([], generated_at=now, briefing_payload={})
    assert state.briefing_lines == []
    assert state.briefing_warnings == []


# ---------------------------------------------------------------------------
# 5. briefing_payload with non-dict items and items missing text -> skipped
# ---------------------------------------------------------------------------

def test_briefing_skips_invalid_items():
    payload = {
        "todo": ["not_a_dict", {"text_zh": ""}, {"text_en": "valid line"}],
        "schedule": [123, {"text_en": "another valid"}],
    }
    lines = _briefing_lines(payload)
    assert lines == ["valid line", "another valid"]


def test_briefing_lines_truncates_to_four():
    """Per-key cap of 3 + overall cap of 4: 6 valid items -> only first 4 kept."""
    payload = {
        "todo": [{"text_en": f"todo{i}"} for i in range(3)],
        "schedule": [{"text_en": f"schedule{i}"} for i in range(3)],
    }
    lines = _briefing_lines(payload)
    assert lines == ["todo0", "todo1", "todo2", "schedule0"]
    assert len(lines) == 4


# ---------------------------------------------------------------------------
# 6. Naive due_at + tz-aware now -> _localize adds now's tzinfo
# ---------------------------------------------------------------------------

def test_localize_naive_due_at_gets_tz():
    now = datetime(2026, 6, 30, 14, 0, tzinfo=timezone(timedelta(hours=8)))
    naive_due = datetime(2026, 6, 30, 10, 0)  # naive, same calendar day as now in +8
    localized = _localize(naive_due, now)
    assert localized is not None
    assert localized.tzinfo == now.tzinfo
    # Same calendar day -> due_today
    t = Task(id="t6", title="naive due", due_at=naive_due, source="test", tags=[])
    state = build_dashboard_state([t], generated_at=now)
    assert state.snapshot.due_today == 1


# ---------------------------------------------------------------------------
# 7. tz-aware due_at with different tz -> astimezone converts correctly
# ---------------------------------------------------------------------------

def test_localize_different_tz_astimezone():
    tz_plus8 = timezone(timedelta(hours=8))
    now = datetime(2026, 6, 30, 14, 0, tzinfo=tz_plus8)  # 14:00 +08
    # UTC 05:00 = 13:00 +08, which is < now (14:00 +08) -> overdue
    utc_due = datetime(2026, 6, 30, 5, 0, tzinfo=timezone.utc)
    t = Task(id="t7", title="utc due", due_at=utc_due, source="test", tags=[])
    state = build_dashboard_state([t], generated_at=now)
    assert state.snapshot.overdue == 1


# ---------------------------------------------------------------------------
# 7b. Cross-day naive due_at boundary: now 02:00 +08, naive due prev day 23:00
# -> _localize stamps +08, different calendar day, due < now -> overdue not today
# ---------------------------------------------------------------------------

def test_localize_naive_cross_day_boundary():
    tz_plus8 = timezone(timedelta(hours=8))
    now = datetime(2026, 6, 30, 2, 0, tzinfo=tz_plus8)  # 02:00 +08
    naive_due = datetime(2026, 6, 29, 23, 0)  # naive, previous calendar day 23:00
    t = Task(id="t7b", title="cross day", due_at=naive_due, source="test", tags=[])
    state = build_dashboard_state([t], generated_at=now)
    assert state.snapshot.overdue == 1
    assert state.snapshot.due_today == 0


# ---------------------------------------------------------------------------
# 8. _when_text five branches
# ---------------------------------------------------------------------------

def test_when_text_overdue():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="w1", title="overdue", due_at=now - timedelta(days=1), source="s", tags=[])
    assert _when_text(t, now).startswith("Overdue")


def test_when_text_today():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="w2", title="today", due_at=now + timedelta(hours=3), source="s", tags=[])
    assert _when_text(t, now).startswith("Today")


def test_when_text_tomorrow():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="w3", title="tomorrow", due_at=now + timedelta(days=1), source="s", tags=[])
    assert _when_text(t, now).startswith("Tomorrow")


def test_when_text_in_n_days():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="w4", title="in5days", due_at=now + timedelta(days=5), source="s", tags=[])
    wt = _when_text(t, now)
    assert wt.startswith("In 5 days")


def test_when_text_plain_beyond_7d():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    t = Task(id="w5", title="later", due_at=now + timedelta(days=10), source="s", tags=[])
    wt = _when_text(t, now)
    assert not wt.startswith("In")
    assert not wt.startswith("Overdue")
    assert not wt.startswith("Today")
    assert not wt.startswith("Tomorrow")
    # Should just be strftime format "%b %d %H:%M" (locale-safe expected)
    assert wt == t.due_at.strftime("%b %d %H:%M")


# ---------------------------------------------------------------------------
# 9. upcoming_groups five buckets
# ---------------------------------------------------------------------------

def test_upcoming_groups_all_five_buckets():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    tasks = [
        _task(title="overdue", due_at=now - timedelta(days=1)),
        _task(title="today", due_at=now + timedelta(hours=3)),
        _task(title="tomorrow", due_at=now + timedelta(days=1)),
        _task(title="in5d", due_at=now + timedelta(days=5)),
        _task(title="later", due_at=now + timedelta(days=10)),
    ]
    state = build_dashboard_state(tasks, generated_at=now)
    labels = [g.label for g in state.upcoming_groups]
    assert labels == ["Overdue", "Today", "Tomorrow", "Next 7 Days", "Later"]


# ---------------------------------------------------------------------------
# 10. focus_items sort order: overdue before today before later
# ---------------------------------------------------------------------------

def test_focus_items_sort_order():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    tasks = [
        _task(title="later", due_at=now + timedelta(days=10)),
        _task(title="today", due_at=now + timedelta(hours=3)),
        _task(title="overdue", due_at=now - timedelta(hours=2)),
    ]
    state = build_dashboard_state(tasks, generated_at=now)
    titles = [item.title for item in state.focus_items]
    assert titles.index("overdue") < titles.index("today") < titles.index("later")


# ---------------------------------------------------------------------------
# 11. _greeting four time periods
# ---------------------------------------------------------------------------

def test_greeting_night():
    now = datetime(2026, 6, 30, 3, 0, tzinfo=timezone.utc)
    assert _greeting(now) == "Night shift dashboard"


def test_greeting_morning():
    now = datetime(2026, 6, 30, 9, 0, tzinfo=timezone.utc)
    assert _greeting(now) == "Morning dashboard"


def test_greeting_afternoon():
    now = datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)
    assert _greeting(now) == "Afternoon dashboard"


def test_greeting_evening():
    now = datetime(2026, 6, 30, 20, 0, tzinfo=timezone.utc)
    assert _greeting(now) == "Evening dashboard"


# ---------------------------------------------------------------------------
# 12. _relative_update_text branches
# ---------------------------------------------------------------------------

def test_relative_update_text_none():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    assert _relative_update_text(None, now) == "No synced task snapshot yet"


def test_relative_update_text_just_now():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    assert _relative_update_text(now - timedelta(seconds=30), now) == "Updated just now"


def test_relative_update_text_minutes_ago():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    assert _relative_update_text(now - timedelta(minutes=5), now) == "Updated 5m ago"


def test_relative_update_text_hours_ago():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    assert _relative_update_text(now - timedelta(hours=3), now) == "Updated 3h ago"


def test_relative_update_text_days_ago():
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(days=2)
    result = _relative_update_text(last, now)
    assert result.startswith("Updated on ")
    assert last.strftime("%Y-%m-%d %H:%M") in result


# ---------------------------------------------------------------------------
# 13. _pipeline_summary branches
# ---------------------------------------------------------------------------

def test_pipeline_summary_ok():
    assert _pipeline_summary(True, []) == "Latest pipeline run completed successfully."


def test_pipeline_summary_failed_with_names():
    steps = [
        {"name": "aggregation", "ok": False},
        {"name": "briefing", "ok": True},
    ]
    result = _pipeline_summary(False, steps)
    assert "Pipeline needs attention: aggregation" == result


def test_pipeline_summary_failed_no_failed_steps():
    steps = [{"name": "briefing", "ok": True}]
    assert _pipeline_summary(False, steps) == "Pipeline reported a failure."


def test_pipeline_summary_none():
    assert _pipeline_summary(None, []) == "No pipeline report found yet."


# ---------------------------------------------------------------------------
# 14. render_task_card with and without url/snippet/course
# ---------------------------------------------------------------------------

def test_render_task_card_with_url_snippet_course():
    item = DashboardItem(
        title="My Task",
        source="gmail",
        course="CS202",
        when_text="Today",
        bucket="today",
        url="https://example.com",
        snippet="Important detail",
    )
    html = render_task_card(item)
    assert '<a class="task-link"' in html
    assert "https://example.com" in html
    assert '<div class="task-snippet">Important detail</div>' in html
    assert "CS202" in html


def test_render_task_card_without_url_snippet_course():
    item = DashboardItem(
        title="Plain",
        source="brightspace",
        course=None,
        when_text="No time",
        bucket="upcoming",
        url=None,
        snippet=None,
    )
    html = render_task_card(item)
    assert "No course" in html
    assert '<a class="task-link"' not in html
    assert '<div class="task-snippet"' not in html


# ---------------------------------------------------------------------------
# 15. render_task_card XSS: title with <script> is escaped
# ---------------------------------------------------------------------------

def test_render_task_card_xss_escaped():
    item = DashboardItem(
        title="<script>alert('xss')</script>",
        source="s",
        course="c",
        when_text="w",
        bucket="b",
        url=None,
        snippet=None,
    )
    html = render_task_card(item)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
