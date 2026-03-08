from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
import html
import re
import textwrap
import os
import sys
from itertools import groupby

import subprocess
import streamlit as st

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nexus.aggregation import (  # noqa: E402
    check_brightspace_feeds,
    run_aggregation,
    sync_google_calendar,
)
from nexus.config import default_config  # noqa: E402
from nexus.storage import load_feeds, load_tasks, tasks_last_updated  # noqa: E402


def _missing_ical_courses():
    """Identifies courses that have RSS feeds but are missing iCal feeds (common config error)."""
    feeds = load_feeds()
    rss_courses = {
        feed.course or feed.name
        for feed in feeds
        if feed.enabled and feed.kind == "brightspace_rss"
    }
    ical_courses = {
        feed.course or feed.name
        for feed in feeds
        if feed.enabled and feed.kind in {"brightspace_ical", "ical_file"}
    }
    missing = sorted(course for course in rss_courses if course not in ical_courses)
    return missing


def _localize_dt(value: datetime | None, now_local: datetime) -> datetime | None:
    """Converts a datetime to the local timezone."""
    if value is None:
        return None
    if value.tzinfo:
        return value.astimezone(now_local.tzinfo)
    return value.replace(tzinfo=now_local.tzinfo)


def _format_dt(value: datetime | None, now_local: datetime) -> str:
    """Formats datetime as a readable string (YYYY-MM-DD HH:MM)."""
    local = _localize_dt(value, now_local)
    if not local:
        return "TBD"
    return local.strftime("%Y-%m-%d %H:%M %Z")

def _task_title(task) -> str:
    """Clean up task title."""
    return task.title.strip()


def _is_course_reminder(task) -> bool:
    """Checks if a task is a general course notification (not a deadline)."""
    tags = set(task.tags or [])
    if task.source == "gmail":
        return "course_notification" in tags
    if task.source == "brightspace":
        return "announcement" in tags
    return False


def _display_name() -> str | None:
    """Gets the user's display name from environment variables."""
    name = os.getenv("NEXUS_DISPLAY_NAME", "").strip()
    return name or None


def _weekday_en(now_local: datetime) -> str:
    """Returns the English abbreviation for the current weekday."""
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return weekdays[now_local.weekday()]


def _format_abs_date(value: datetime, now_local: datetime) -> str:
    """Formats date as 'Month Day Hour:Minute'."""
    local = _localize_dt(value, now_local)
    if not local:
        return "Unknown time"
    return local.strftime("%b %d %H:%M")


def _relative_due_text(value: datetime | None, now_local: datetime) -> str:
    """Generates relative time text (e.g., 'Tomorrow', 'In 3 days')."""
    local = _localize_dt(value, now_local)
    if not local:
        return "No time set"
    today = now_local.date()
    due_date = local.date()
    day_diff = (due_date - today).days
    if day_diff == 0:
        if local < now_local:
            hours = max(1, int((now_local - local).total_seconds() // 3600))
            return f"Overdue by {hours}h"
        return f"Today {local.strftime('%H:%M')}"
    if day_diff == 1:
        return f"Tomorrow {local.strftime('%H:%M')}"
    if day_diff == -1:
        return f"Yesterday {local.strftime('%H:%M')}"
    if day_diff > 1:
        return f"In {day_diff} days"
    return f"Overdue by {abs(day_diff)} days"


def _get_greeting(hour: int) -> str:
    """Returns a greeting based on the time of day."""
    if 23 <= hour or hour < 6:
        return "🌙 Late night — get some rest"
    if 6 <= hour < 12:
        return "☀️ Good morning"
    if 12 <= hour < 18:
        return "👋 Good afternoon"
    return "🌆 Good evening"


def _is_deadline_task(task) -> bool:
    """Checks if a task represents a hard deadline (exam/assignment)."""
    tags = set(task.tags or [])
    if {"assignment", "exam", "deadline"} & tags:
        return True
    return False


def _count_deadlines_in_range(tasks, start: datetime, end: datetime, now_local: datetime) -> int:
    """Counts how many deadlines fall within a specific time range."""
    count = 0
    for task in tasks:
        if not _is_deadline_task(task):
            continue
        due = _localize_dt(task.due_at, now_local)
        if due and start <= due < end:
            count += 1
    return count


def _get_smart_tip(deadlines, now_local: datetime) -> str | None:
    """Generates a helpful tip based on upcoming workload."""
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    today = _count_deadlines_in_range(deadlines, today_start, tomorrow_start, now_local)
    tomorrow = _count_deadlines_in_range(deadlines, tomorrow_start, tomorrow_end, now_local)
    week = _count_deadlines_in_range(deadlines, today_start, week_end, now_local)

    if tomorrow >= 2:
        return f"⚠️ {tomorrow} deadlines tomorrow — consider handling tonight"
    if today > 0 and now_local.hour > 20:
        return f"🔥 {today} tasks still due today"
    if week >= 5:
        return f"📊 {week} tasks this week — plan ahead"
    if tomorrow == 0 and today == 0:
        return "✨ No deadlines tomorrow — push long-term work"
    return None


_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_holiday_range(title: str, now_local: datetime) -> tuple[datetime.date, datetime.date] | None:
    """Extracts date ranges from holiday titles (e.g., 'Jan 1 - Jan 3')."""
    title = title.strip()
    if not title:
        return None
    m = re.search(
        r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"\\s*(?P<d1>\\d{1,2})\\s*[-–]\\s*"
        r"(?:(?P<mon2>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\\s*)?"
        r"(?P<d2>\\d{1,2})",
        title,
        re.IGNORECASE,
    )
    if m:
        mon = _MONTHS[m.group("mon").lower()]
        mon2 = _MONTHS[m.group("mon2").lower()] if m.group("mon2") else mon
        d1 = int(m.group("d1"))
        d2 = int(m.group("d2"))
        year = now_local.year
        start = datetime(year, mon, d1).date()
        end = datetime(year, mon2, d2).date()
        return start, end
    m = re.search(r"(?P<m1>\\d{1,2})/(?P<d1>\\d{1,2})\\s*[-–]\\s*(?P<m2>\\d{1,2})/(?P<d2>\\d{1,2})", title)
    if m:
        year = now_local.year
        start = datetime(year, int(m.group("m1")), int(m.group("d1"))).date()
        end = datetime(year, int(m.group("m2")), int(m.group("d2"))).date()
        return start, end
    return None


def _is_holiday_task(task) -> bool:
    """Checks if a task is a holiday event."""
    tags = set(task.tags or [])
    title = (task.title or "").lower()
    if "holiday" in tags:
        return True
    holiday_markers = ("holiday", "festival", "break", "vacation", "节", "假期")
    return any(marker in title for marker in holiday_markers)


def _holiday_ranges(tasks, now_local: datetime) -> list[tuple[datetime.date, datetime.date]]:
    """Finds all holiday date ranges in the task list."""
    ranges: list[tuple[datetime.date, datetime.date]] = []
    for task in tasks:
        if not _is_holiday_task(task):
            continue
        parsed = _parse_holiday_range(task.title, now_local)
        if parsed:
            ranges.append(parsed)
            continue
        if task.due_at:
            due = _localize_dt(task.due_at, now_local)
            if due:
                ranges.append((due.date(), due.date()))
    return ranges


def _get_date_context(
    weekday: int, hour: int, is_before_holiday: bool, is_last_holiday_day: bool
) -> str | None:
    """Provides context-aware messages (e.g., 'Friday', 'Before Holiday')."""
    if weekday == 4:
        return "📅 Friday — check weekend work"
    if weekday == 6 and hour >= 18:
        return "📚 Sunday night — prep for Monday"
    if is_before_holiday:
        return "🎉 Day before holiday"
    if is_last_holiday_day:
        return "⏰ Last day of holiday — check progress"
    return None


def _is_course_update(task) -> bool:
    """Alias for checking course reminders."""
    return _is_course_reminder(task)


def _is_meeting_task(task) -> bool:
    """Checks if a task is a meeting or office hour."""
    title = (task.title or "").lower()
    meeting_markers = ("meeting", "office hour", "office hours", "call", "sync", "standup", "会议", "答疑")
    return any(marker in title for marker in meeting_markers)


def _event_type(task) -> str:
    """Categorizes a task for UI styling (exam, assignment, meeting, etc.)."""
    tags = set(task.tags or [])
    if _is_grade_task(task):
        return "grade"
    if _is_course_update(task):
        return "course"
    if _is_holiday_task(task):
        return "holiday"
    if "exam" in tags or "midterm" in (task.title or "").lower() or "final" in (task.title or "").lower():
        return "exam"
    if "assignment" in tags or "homework" in (task.title or "").lower():
        return "assignment"
    if _is_meeting_task(task):
        return "meeting"
    return "event"


def _event_icon(event_type: str) -> str:
    """Returns an emoji icon for the event type."""
    return {
        "assignment": "📝",
        "exam": "📄",
        "grade": "📊",
        "holiday": "🎉",
        "meeting": "👥",
        "course": "📬",
    }.get(event_type, "📌")


def _is_grade_task(task) -> bool:
    tags = set(task.tags or [])
    title = (task.title or "").lower()
    return "grade" in tags or title.startswith("[grade]")


def _urgency_class(event_type: str, due_at: datetime | None, now_local: datetime) -> str:
    """Determines the CSS class for urgency (red for overdue/soon, green for future)."""
    if event_type in {"holiday", "course"}:
        return "event-info"
    if not due_at:
        return "event-info"
    due = _localize_dt(due_at, now_local)
    if not due:
        return "event-info"
    delta = due - now_local
    if delta.total_seconds() < 0:
        return "event-overdue"
    if delta <= timedelta(hours=24):
        return "event-urgent-24h"
    if delta <= timedelta(days=3):
        return "event-urgent-3day"
    return "event-urgent-future"


def _render_event_card(task, now_local: datetime, delay_ms: int = 0) -> str:
    """Generates the HTML for a single event card in the UI."""
    event_type = _event_type(task)
    icon = _event_icon(event_type)
    primary_time = task.due_at or task.received_at
    due = _localize_dt(primary_time, now_local)
    urgency = _urgency_class(event_type, task.due_at, now_local)
    classes = ["event-card", urgency, f"event-{event_type}"]
    title_raw = _task_title(task)
    title = html.escape(title_raw)
    relative = _relative_due_text(primary_time, now_local)
    absolute = _format_abs_date(due, now_local) if due else None
    meta = relative if not absolute else f"{relative} · {absolute}"
    meta = html.escape(meta)
    course = html.escape(task.course) if task.course else ""
    snippet = html.escape(task.snippet) if task.snippet else ""
    url = html.escape(task.url) if task.url else ""
    extra_parts = []
    course_line = ""
    if course and (not task.course or task.course not in title_raw):
        course_line = f"<div class=\"event-sub\">{course}</div>"
    if snippet:
        extra_parts.append(snippet)
    if url:
        extra_parts.append(f'<a class="event-link" href="{url}" target="_blank">Source</a>')
    extra_html = ""
    if extra_parts:
        extra_html = f"<div class=\"event-extra\">{' · '.join(extra_parts)}</div>"
    delay_style = f" style=\"animation-delay: {delay_ms}ms;\"" if delay_ms else ""
    return (
        f"<div class=\"{' '.join(classes)}\"{delay_style}>"
        f"<div class=\"event-title\"><span class=\"event-icon\">{icon}</span>{title}</div>"
        f"<div class=\"event-meta\">{meta}</div>"
        f"{course_line}"
        f"{extra_html}"
        "</div>"
    )

# --- Main Streamlit App Execution ---
st.set_page_config(page_title="Nexus", page_icon="🧭", layout="wide")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=JetBrains+Mono:wght@400;600&family=Noto+Sans+SC:wght@400;600;700&display=swap');
:root {
    --bg: #0b0f14;
    --panel: #111826;
    --panel-2: #131c2b;
    --text: #e6edf3;
    --muted: rgba(230, 237, 243, 0.68);
    --border: rgba(255, 255, 255, 0.08);
    --accent: #4cc2ff;
}
html, body, [data-testid="stAppViewContainer"] {
    background:
        radial-gradient(1200px 600px at 10% -10%, rgba(76, 194, 255, 0.18), transparent 60%),
        radial-gradient(900px 500px at 90% 0%, rgba(255, 149, 0, 0.12), transparent 60%),
        var(--bg);
    color: var(--text);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] {
    background: #0e1420;
    border-right: 1px solid var(--border);
}
[data-testid="stSidebar"] * {
    color: var(--text);
}
[data-testid="stSidebar"] .stButton > button,
[data-testid="stSidebar"] button {
    background: rgba(17, 24, 38, 0.75);
    border: 1px solid var(--border);
    color: var(--text);
}
[data-testid="stSidebar"] .stButton > button:hover,
[data-testid="stSidebar"] button:hover {
    border-color: rgba(255, 255, 255, 0.2);
    background: rgba(17, 24, 38, 0.9);
}
[data-testid="stSidebar"] input,
[data-testid="stSidebar"] textarea,
[data-testid="stSidebar"] select {
    background: rgba(17, 24, 38, 0.75);
    color: var(--text);
    border: 1px solid var(--border);
}
[data-testid="stSidebar"] pre,
[data-testid="stSidebar"] code {
    background: rgba(17, 24, 38, 0.75) !important;
    color: var(--text) !important;
    border: 1px solid var(--border);
}
* {
    font-family: "Noto Sans SC", "Manrope", "Helvetica Neue", Arial, sans-serif;
    overflow-wrap: break-word;
}
.block-container { max-width: 100%; padding-top: 2rem; padding-left: 3rem; padding-right: 3rem; }
.status-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    flex-wrap: wrap;
}
.status-title {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: 0.4px;
}
.status-time {
    text-align: right;
    font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    color: var(--muted);
}
.status-time .time-main {
    font-size: 16px;
    font-weight: 600;
}
.status-time .time-sub {
    font-size: 12px;
    margin-top: 2px;
    opacity: 0.9;
}
.status-tips {
    background: rgba(17, 24, 38, 0.6);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 14px;
    padding: 16px 20px;
    margin-bottom: 28px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
}
.tip-line {
    font-size: 14px;
    font-style: italic;
    opacity: 0.85;
    margin: 4px 0;
}
.section-title {
    font-size: 22px;
    font-weight: 700;
    margin: 28px 0 12px;
}
.section-title.section-muted {
    color: var(--muted);
}
.section-subtitle {
    font-size: 16px;
    font-weight: 600;
    color: var(--muted);
    margin: 16px 0 8px;
}
.section-empty {
    color: var(--muted);
    font-size: 14px;
    padding: 8px 0 4px;
}
.date-header {
    font-size: 12px;
    font-weight: 700;
    color: var(--muted);
    margin: 24px 0 8px 2px;
    text-transform: uppercase;
    letter-spacing: 1px;
    opacity: 0.7;
}
.event-card {
    background: rgba(19, 28, 43, 0.6);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-left: 4px solid #34C759;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 16px;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    animation: cardIn 0.35s ease both;
}
.event-card:hover {
    transform: translateY(-3px) scale(1.01);
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.4);
    border-color: rgba(255, 255, 255, 0.15);
}
.event-title {
    font-size: 18px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
}
.event-meta {
    font-size: 14px;
    color: var(--muted);
    margin-top: 6px;
}
.event-sub {
    font-size: 14px;
    color: var(--muted);
    margin-top: 4px;
}
.event-extra {
    font-size: 13px;
    color: var(--muted);
    margin-top: 6px;
    max-height: 0;
    opacity: 0;
    overflow: hidden;
    transition: max-height 0.2s ease, opacity 0.2s ease;
}
.event-card:hover .event-extra {
    max-height: 120px;
    opacity: 1;
}
.event-link { color: var(--accent); text-decoration: none; }
.event-urgent-24h { border-left-color: #FF453A; background: linear-gradient(90deg, rgba(255, 69, 58, 0.1), transparent); }
.event-urgent-3day { border-left-color: #FF9F0A; background: linear-gradient(90deg, rgba(255, 159, 10, 0.08), transparent); }
.event-urgent-future { border-left-color: #32D74B; background: linear-gradient(90deg, rgba(50, 215, 75, 0.06), transparent); }
.event-info { border-left-color: #0A84FF; background: linear-gradient(90deg, rgba(10, 132, 255, 0.06), transparent); }
.event-overdue { border-left-color: #FF453A; background: linear-gradient(90deg, rgba(255, 69, 58, 0.15), transparent); }
.event-exam { border-left-width: 6px; }
.event-meeting { border-style: dashed; }
.event-holiday { background: rgba(0, 122, 255, 0.12); }
.event-course { background: rgba(0, 122, 255, 0.08); }

/* Timeline Specifics */
.timeline-container {
    position: relative;
    margin-top: 10px;
}
/* Fix Expander Background */
div[data-testid="stExpander"] {
    background-color: transparent;
    border: none;
}
div[data-testid="stExpander"] > details {
    background-color: rgba(17, 24, 38, 0.55);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: var(--text);
}
div[data-testid="stExpander"] > details > summary {
    color: var(--text) !important;
    background: rgba(17, 24, 38, 0.6);
    border-radius: 10px;
    padding: 0.35rem 0.5rem;
}
div[data-testid="stExpander"] > details > div {
    color: var(--muted);
}
/* Fix Alert Backgrounds */
div[data-testid="stAlert"] {
    background-color: rgba(17, 24, 38, 0.55);
    backdrop-filter: blur(12px);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 12px;
}
.timeline-block {
    display: flex;
    gap: 16px;
    margin-bottom: 0;
    position: relative;
}
.timeline-time-col {
    width: 50px;
    text-align: right;
    flex-shrink: 0;
    padding-top: 16px;
}
.t-time {
    font-family: "JetBrains Mono", monospace;
    font-size: 13px;
    color: var(--muted);
    font-weight: 600;
}
.timeline-content {
    flex-grow: 1;
    padding-bottom: 24px;
    border-left: 2px solid var(--border);
    padding-left: 20px;
    position: relative;
}
.timeline-dot {
    width: 10px;
    height: 10px;
    background: var(--accent);
    border-radius: 50%;
    position: absolute;
    left: -4px;
    top: 20px;
    border: 2px solid var(--bg);
    z-index: 1;
}
.now-marker {
    display: flex;
    align-items: center;
    gap: 16px;
    margin: 0;
    padding-bottom: 24px;
}
.now-time {
    width: 50px;
    text-align: right;
    color: #FF453A;
    font-weight: 700;
    font-size: 12px;
    font-family: "JetBrains Mono", monospace;
    flex-shrink: 0;
}
.now-line {
    flex-grow: 1;
    height: 2px;
    background: #FF453A;
    position: relative;
    opacity: 0.8;
}
.now-dot {
    width: 8px;
    height: 8px;
    background: #FF453A;
    border-radius: 50%;
    position: absolute;
    left: -4px;
    top: -3px;
}

@keyframes cardIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}
@media (max-width: 720px) {
    .status-bar {
        flex-direction: column;
        align-items: flex-start;
        gap: 6px;
    }
    .status-time {
        text-align: left;
    }
    .event-extra {
        max-height: 200px;
        opacity: 1;
    }
}
</style>
""",
    unsafe_allow_html=True,
)

# Load configuration and data
config = default_config()
now_local = datetime.now().astimezone()
calendar_sync_error = None
if config.google_calendar_sync_minutes > 0:
    last_sync = tasks_last_updated()
    if last_sync is None or now_local - last_sync > timedelta(minutes=config.google_calendar_sync_minutes):
        result = sync_google_calendar()
        if result.errors:
            calendar_sync_error = result.errors[0]
tasks = load_tasks()

# Prepare header info
display_name = _display_name()
greeting = _get_greeting(now_local.hour)
if display_name:
    greeting = f"{greeting}, {display_name}"

cutoff_time = now_local
deadline_tasks = [
    task
    for task in tasks
    if _is_deadline_task(task)
    and _localize_dt(task.due_at, now_local)
    and _localize_dt(task.due_at, now_local) >= cutoff_time
]
holiday_ranges = _holiday_ranges(tasks, now_local)
is_before_holiday = any(start == now_local.date() + timedelta(days=1) for start, _ in holiday_ranges)
is_last_holiday_day = any(end == now_local.date() for _, end in holiday_ranges)
smart_tip = _get_smart_tip(deadline_tasks, now_local)
date_context = _get_date_context(
    now_local.weekday(),
    now_local.hour,
    is_before_holiday=is_before_holiday,
    is_last_holiday_day=is_last_holiday_day,
)
tip_lines = [tip for tip in (smart_tip, date_context) if tip]
if not tip_lines:
    tip_lines = ["✅ No urgent items today"]

time_display = f"{now_local.strftime('%H:%M')} {_weekday_en(now_local)}"
date_display = now_local.strftime("%b %d")

# --- Task Filtering & Sorting ---
# Split tasks into Schedule (Timeline) and Deadlines (Action)
schedule_items = []
deadline_items = []

for task in tasks:
    if not task.due_at:
        continue
    
    # Filter out past items (older than 2 hours ago for schedule, older than now for deadlines)
    local_due = _localize_dt(task.due_at, now_local)
    if not local_due:
        continue
        
    etype = _event_type(task)
    
    # Schedule: Today's classes, meetings, events
    if etype in {"course", "meeting", "event", "holiday"}:
        if local_due.date() == now_local.date():
            schedule_items.append(task)
    
    # Deadlines: Assignments, Exams (Future)
    elif etype in {"assignment", "exam"}:
        if local_due >= now_local:
            deadline_items.append(task)

schedule_items.sort(key=lambda t: _localize_dt(t.due_at, now_local))
deadline_items.sort(key=lambda t: _localize_dt(t.due_at, now_local))

# Group deadlines by urgency
urgent: list = []
upcoming: list = []
later: list = []
for task in deadline_items:
    due = _localize_dt(task.due_at, now_local)
    if not due:
        continue
    if due <= now_local + timedelta(days=3):
        urgent.append(task)
    elif due <= now_local + timedelta(days=7):
        upcoming.append(task)
    else:
        later.append(task)

# Filter recent course updates
course_updates: list = []
course_cutoff = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
for task in tasks:
    if not _is_course_update(task):
        continue
    timestamp = _localize_dt(task.received_at or task.due_at, now_local)
    if not timestamp or timestamp < course_cutoff:
        continue
    course_updates.append(task)
course_updates.sort(
    key=lambda t: _localize_dt(t.received_at or t.due_at, now_local) or now_local,
    reverse=True,
)

grade_updates = [task for task in tasks if _is_grade_task(task)]
grade_updates.sort(
    key=lambda t: _localize_dt(t.received_at or t.due_at, now_local) or now_local,
    reverse=True,
)

tab_dash, tab_study = st.tabs(["📋  Dashboard", "🎓  Study Assistant"])

with tab_dash:
    left_panel, right_panel = st.columns([1, 1], gap="large")

# --- Sidebar ---
with st.sidebar:
    st.markdown(
        f"""
    <div class="status-bar">
      <div class="status-title">Nexus</div>
      <div class="status-time">
        <div class="time-main">{html.escape(time_display)}</div>
        <div class="time-sub">{html.escape(date_display)}</div>
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    with st.expander("System Controls", expanded=True):
        if calendar_sync_error:
            st.warning(f"Calendar sync issue: {calendar_sync_error}")

        st.subheader("Today Snapshot")
        st.write(f"Tasks in board: {len(tasks)}")
        st.write(f"Today's course updates: {len(course_updates)}")
        st.write(f"Grade items: {len(grade_updates)}")

        st.subheader("Aggregation")
        if st.button("Run Aggregation (Google + Brightspace)"):
            with st.spinner("Aggregating..."):
                result = run_aggregation(include_google=True)
            if result.errors:
                for err in result.errors:
                    st.error(err)
            st.success(f"Aggregated {len(result.tasks)} items.")

            window_days = 14
            display_tasks = result.tasks

            gmail_tasks = [t for t in display_tasks if t.source == "gmail"]
            cal_tasks = [t for t in display_tasks if t.source == "gcal"]
            bspace_tasks = [
                t
                for t in display_tasks
                if t.source == "brightspace" and "llm_only" not in t.tags
            ]
            st.caption(f"Showing next {window_days} days of items.")

            if gmail_tasks:
                course_mail = [t for t in gmail_tasks if "course_notification" in t.tags]
                other_mail = [t for t in gmail_tasks if "course_notification" not in t.tags]
                if course_mail:
                    st.markdown("**Gmail (Course Notifications)**")
                    grouped_mail: dict[str, list] = {}
                    for t in course_mail:
                        course = t.course or "Uncategorized"
                        grouped_mail.setdefault(course, []).append(t)
                    for course, items in sorted(grouped_mail.items()):
                        st.markdown(f"**{course}**")
                        for t in items:
                            st.write(f"- {t.title}")
                if other_mail:
                    st.markdown("**Gmail (Other)**")
                    for t in other_mail:
                        st.write(f"- {t.title}")

            if cal_tasks:
                st.markdown("**Calendar (Next 7 Days)**")
                for t in cal_tasks:
                    when = _format_dt(t.due_at, now_local)
                    st.write(f"- {when} — {t.title}")

            if bspace_tasks:
                st.markdown("**Brightspace**")
                grouped: dict[str, list] = {}
                for t in bspace_tasks:
                    course = t.course or "Uncategorized"
                    grouped.setdefault(course, []).append(t)
                for course, items in sorted(grouped.items()):
                    st.markdown(f"**{course}**")
                    for t in items:
                        when = _format_dt(t.due_at, now_local)
                        st.write(f"- {when} — {t.title}")

        st.divider()
        st.subheader("Google Status")
        cred_path = config.google_credentials_path or Path("")
        token_path = config.google_token_path or Path("")
        st.write(f"Client secret: {'OK' if cred_path.exists() else 'Missing'}")
        st.write(f"Token: {'OK' if token_path.exists() else 'Missing'}")
        st.code("python -m nexus.google_auth", language="bash")

        st.divider()
        st.subheader("Feed Diagnostics")
        missing_courses = _missing_ical_courses()
        if missing_courses:
            st.warning(
                "Missing Brightspace iCal feeds for courses (assignments will not appear): "
                + ", ".join(missing_courses)
            )
        if st.button("Test Brightspace Feeds"):
            statuses = check_brightspace_feeds()
            if not statuses:
                st.info("No feeds configured. Update data/feeds.json.")
            for status in statuses:
                label = f"{status.name} ({status.kind})"
                if not status.enabled:
                    st.write(f"⏸️ {label} — disabled")
                elif status.ok:
                    st.write(f"✅ {label} — items: {status.item_count}")
                else:
                    st.write(f"❌ {label} — error: {status.error}")

        st.divider()
        st.subheader("Archive Sync")
        st.caption("使用浏览器登录 Brightspace，抓取作业/日历/成绩并同步到任务列表")
        if st.button("▶ Run Archive Sync"):
            with st.spinner("打开浏览器...请在弹出窗口完成 Duo 验证"):
                _src_dir = str(SRC_ROOT)
                _env = {**os.environ, "PYTHONPATH": _src_dir}
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "nexus.archive_sync"],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        env=_env,
                    )
                except subprocess.TimeoutExpired:
                    st.error("Archive Sync timed out (300 s). Browser may still be open.")
                    proc = None

            if proc is not None:
                stdout = proc.stdout.strip()
                if proc.stderr:
                    with st.expander("Debug log"):
                        st.code(proc.stderr, language="text")
                if proc.returncode != 0 and not stdout:
                    st.error(f"Archive Sync failed (exit {proc.returncode}).")
                elif stdout:
                    import json as _json
                    try:
                        result = _json.loads(stdout)
                        if result.get("status") == "success":
                            new_count = result.get("tasks_new", len(result.get("tasks", [])))
                            merged = result.get("tasks_merged", "?")
                            archives = result.get("data", [])
                            failures = result.get("archive_failures", [])
                            st.success(
                                f"同步完成：新增 {new_count} 条，tasks.json 共 {merged} 条。"
                            )
                            st.caption(
                                f"归档文件：{len(archives)}，失败：{len(failures)}"
                            )
                            if archives:
                                with st.expander("Archived Files"):
                                    for item in archives:
                                        course = item.get("course") or "Unknown Course"
                                        name = item.get("original_name") or "Unnamed"
                                        due = item.get("due_date") or "N/A"
                                        st.write(f"- [{course}] {name} (due: {due})")
                            if failures:
                                with st.expander("Archive Failures"):
                                    for item in failures:
                                        course = item.get("course") or "Unknown Course"
                                        title = item.get("assignment_title") or "Unknown Assignment"
                                        err = item.get("error") or "Unknown error"
                                        st.write(f"- [{course}] {title}: {err}")
                        else:
                            st.error(f"Archive Sync error: {result.get('message', stdout)}")
                    except _json.JSONDecodeError:
                        st.error(f"Unexpected output: {stdout[:300]}")

        st.divider()
        st.subheader("Knowledge Query")
        with st.expander("P2 Debug Panel", expanded=False):
            query_text = st.text_input("Query", value="", key="knowledge_query_text")
            query_course = st.text_input("Course Filter (optional)", value="", key="knowledge_query_course")
            query_doc_type = st.text_input("Doc Type Filter (optional)", value="", key="knowledge_query_doc_type")
            query_top_k = st.slider("Top K", min_value=1, max_value=10, value=5, step=1)
            if st.button("Run Knowledge Query"):
                if not query_text.strip():
                    st.warning("Please enter a query first.")
                else:
                    try:
                        from nexus.knowledge.query import query_knowledge

                        summary = query_knowledge(
                            Path("data/chroma"),
                            query_text=query_text.strip(),
                            n_results=query_top_k,
                            course_id=query_course.strip() or None,
                            doc_type=query_doc_type.strip() or None,
                        )
                        if not summary.items:
                            st.info("No results found.")
                        else:
                            st.success(f"Found {len(summary.items)} result(s).")
                            for idx, item in enumerate(summary.items, start=1):
                                distance = "n/a" if item.distance is None else f"{item.distance:.4f}"
                                st.markdown(f"**#{idx}** distance={distance}")
                                st.caption(
                                    f"file={item.metadata.get('file_name', 'unknown')} | "
                                    f"course={item.metadata.get('course_id', 'unknown')} | "
                                    f"type={item.metadata.get('doc_type', 'unknown')}"
                                )
                                st.write(item.text[:500])
                    except Exception as exc:
                        st.error(f"Knowledge query failed: {exc}")

# --- Left Panel: Timeline ---
with left_panel:
    st.markdown('<div class="section-title" style="margin-top: 0;">Welcome to Nexus</div>', unsafe_allow_html=True)
    
    tip_html = "".join(f'<div class="tip-line">{html.escape(line)}</div>' for line in [greeting, *tip_lines])
    st.markdown(f'<div class="status-tips">{tip_html}</div>', unsafe_allow_html=True)

    # Render Timeline
    timeline_html = []
    
    # Insert "Now" marker logic
    now_inserted = False
    
    def _render_now():
        return textwrap.dedent(
            """
            <div class="now-marker">
                <div class="now-time">NOW</div>
                <div class="now-line"><div class="now-dot"></div></div>
            </div>
            """
        ).strip()

    if not schedule_items:
        timeline_html.append(_render_now())
        timeline_html.append('<div class="section-empty" style="padding-left: 66px;">No more events today</div>')
    else:
        for task in schedule_items:
            due = _localize_dt(task.due_at, now_local)
            if not now_inserted and due > now_local:
                timeline_html.append(_render_now())
                now_inserted = True
            
            time_str = due.strftime("%H:%M")
            card_html = _render_event_card(task, now_local)
            
            block = textwrap.dedent(
                f"""
                <div class="timeline-block">
                    <div class="timeline-time-col"><div class="t-time">{time_str}</div></div>
                    <div class="timeline-content">
                        <div class="timeline-dot"></div>
                        {card_html}
                    </div>
                </div>
                """
            ).strip()
            timeline_html.append(block)
        
        if not now_inserted:
            timeline_html.append(_render_now())

    st.markdown(
        f'<div class="timeline-container">{"\n".join(timeline_html)}</div>',
        unsafe_allow_html=True,
    )

# --- Right Panel: Deadlines ---
with right_panel:
    st.markdown(
        '<div class="section-title" style="margin-top: 0;">Upcoming Events</div>',
        unsafe_allow_html=True,
    )
    if not urgent:
        st.markdown(
            '<div class="section-empty">No urgent items</div>', unsafe_allow_html=True
        )
    else:
        for idx, task in enumerate(urgent):
            st.markdown(
                _render_event_card(task, now_local, delay_ms=idx * 40),
                unsafe_allow_html=True,
            )

    st.markdown(
        '<div class="section-title section-muted">📅 Upcoming</div>',
        unsafe_allow_html=True,
    )
    if not upcoming:
        st.markdown(
            '<div class="section-empty">Nothing new in the next 7 days</div>',
            unsafe_allow_html=True,
        )
    else:
        def _get_date_label(t):
            dt = _localize_dt(t.due_at, now_local)
            if not dt: return "Later"
            delta = (dt.date() - now_local.date()).days
            if delta == 0: return "Today"
            if delta == 1: return "Tomorrow"
            if delta < 7: return dt.strftime("%A")
            return dt.strftime("%b %d")

        for label, group in groupby(upcoming, key=_get_date_label):
            st.markdown(f'<div class="date-header">{label}</div>', unsafe_allow_html=True)
            for idx, task in enumerate(group):
                st.markdown(
                    _render_event_card(task, now_local, delay_ms=idx * 40),
                    unsafe_allow_html=True,
                )

    with st.expander("Other", expanded=False):
        if later:
            st.markdown(
                '<div class="section-subtitle">Later</div>', unsafe_allow_html=True
            )
            for idx, task in enumerate(later):
                st.markdown(
                    _render_event_card(task, now_local, delay_ms=idx * 30),
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="section-empty">No later items</div>', unsafe_allow_html=True
            )

        if course_updates:
            st.markdown(
                '<div class="section-subtitle">Course Updates</div>', unsafe_allow_html=True
            )
            for idx, task in enumerate(course_updates):
                st.markdown(
                    _render_event_card(task, now_local, delay_ms=idx * 30),
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="section-empty">No course updates today</div>',
                unsafe_allow_html=True,
            )

        if grade_updates:
            st.markdown(
                '<div class="section-subtitle">Grades</div>', unsafe_allow_html=True
            )
            for idx, task in enumerate(grade_updates[:20]):
                st.markdown(
                    _render_event_card(task, now_local, delay_ms=idx * 30),
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="section-empty">No grade updates</div>',
                unsafe_allow_html=True,
            )

# ─────────────────────────────────────────────────────────────────────────────
# Study Assistant Tab (Phase 3 QAPipeline integration)
# ─────────────────────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@st.cache_resource
def _get_qa_pipeline():
    """初始化 QAPipeline，每个 Streamlit 进程只创建一次（cache_resource）。"""
    import json as _json
    from nexus.knowledge.qa_pipeline import QAPipeline

    # 优先读 Streamlit secrets，其次读 JSON 文件，最后读环境变量
    qwen_key = st.secrets.get("QWEN_API_KEY", "") if hasattr(st, "secrets") else ""
    if not qwen_key:
        key_file = _DATA_DIR / "QWEN_API_KEY.json"
        if key_file.exists():
            try:
                qwen_key = _json.loads(key_file.read_text())["QWEN_API_KEY"]
            except Exception:
                pass
    qwen_key = qwen_key or os.getenv("QWEN_API_KEY") or ""

    pipeline = QAPipeline(
        chroma_dir=_DATA_DIR / "chroma",
        sqlite_path=_DATA_DIR / "nexus.db",
        tasks_path=_DATA_DIR / "tasks.json",
        qwen_api_key=qwen_key,
    )
    return pipeline, qwen_key


def _profile_badge(label: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;background:{color};color:#fff;'
        f'border-radius:6px;padding:2px 8px;font-size:12px;margin:2px;">{html.escape(label)}</span>'
    )


with tab_study:
    st.markdown(
        """
        <style>
        .qa-answer { line-height: 1.7; }
        .qa-source-row { font-size: 12px; color: rgba(230,237,243,0.6); margin: 2px 0; }
        .profile-section { background: #111826; border-radius: 10px; padding: 14px;
                           border: 1px solid rgba(255,255,255,0.08); margin-bottom: 12px; }
        .profile-label { font-size: 11px; color: rgba(230,237,243,0.5);
                         text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── 初始化 session state ──
    if "qa_session_id" not in st.session_state:
        import uuid as _uuid
        st.session_state.qa_session_id = _uuid.uuid4().hex[:8]
    if "qa_messages" not in st.session_state:
        st.session_state.qa_messages = []  # list of {role, content, sources, expanded_queries}

    # ── 加载 pipeline ──
    try:
        _pipeline, _qwen_key = _get_qa_pipeline()
        _pipeline_ok = bool(_qwen_key)
    except Exception as _e:
        _pipeline_ok = False
        _pipeline = None
        st.error(f"QAPipeline 初始化失败: {_e}")

    # ── Layout: chat(2/3) + profile(1/3) ──
    chat_col, profile_col = st.columns([2, 1], gap="large")

    with chat_col:
        st.markdown('<div class="section-title" style="margin-top:0;">Study Assistant</div>',
                    unsafe_allow_html=True)

        # 课程选择器
        _known_courses: list[str] = []
        if _pipeline_ok:
            try:
                _known_courses = _pipeline.get_profile().get("courses") or []
            except Exception:
                pass
        _course_options = _known_courses or ["CS202_OS"]
        _selected_course = st.selectbox(
            "Course",
            options=_course_options,
            index=0,
            label_visibility="collapsed",
            key="qa_course_select",
        )

        # 注册课程到 profile（首次访问）
        if _pipeline_ok and _selected_course not in _known_courses:
            try:
                _pipeline.add_course(_selected_course)
            except Exception:
                pass

        # 对话历史渲染
        for _msg in st.session_state.qa_messages:
            with st.chat_message(_msg["role"]):
                st.markdown(_msg["content"], unsafe_allow_html=False)
                if _msg.get("sources"):
                    with st.expander(f"Sources ({len(_msg['sources'])})", expanded=False):
                        for _src in _msg["sources"]:
                            score_str = f"{_src['score']:.3f}"
                            st.markdown(
                                f'<div class="qa-source-row">'
                                f'[{score_str}] <b>{html.escape(_src["topic"])}</b>'
                                f' — {html.escape(_src["file"])}</div>',
                                unsafe_allow_html=True,
                            )
                if _msg.get("expanded_queries") and len(_msg["expanded_queries"]) > 1:
                    with st.expander("Query expansion", expanded=False):
                        for _q in _msg["expanded_queries"]:
                            st.caption(_q)

        # 聊天输入
        if _qa_prompt := st.chat_input(
            "Ask about your course material...",
            disabled=not _pipeline_ok,
        ):
            # 追加用户消息
            st.session_state.qa_messages.append({"role": "user", "content": _qa_prompt})
            with st.chat_message("user"):
                st.markdown(_qa_prompt)

            # 调用 QAPipeline
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        _resp = _pipeline.ask(
                            _qa_prompt,
                            session_id=st.session_state.qa_session_id,
                            course_id=_selected_course,
                        )
                        _answer = _resp.answer
                        _sources = [
                            {
                                "topic": str(s.topic),
                                "file": str(s.source_file),
                                "score": s.score,
                            }
                            for s in _resp.sources
                        ]
                        _expanded = _resp.expanded_queries
                        _warnings = _resp.warnings
                    except Exception as _exc:
                        _answer = f"⚠️ Error: {_exc}"
                        _sources = []
                        _expanded = []
                        _warnings = []

                st.markdown(_answer)
                if _sources:
                    with st.expander(f"Sources ({len(_sources)})", expanded=False):
                        for _src in _sources:
                            st.markdown(
                                f'<div class="qa-source-row">'
                                f'[{_src["score"]:.3f}] <b>{html.escape(_src["topic"])}</b>'
                                f' — {html.escape(_src["file"])}</div>',
                                unsafe_allow_html=True,
                            )
                if _expanded and len(_expanded) > 1:
                    with st.expander("Query expansion", expanded=False):
                        for _q in _expanded:
                            st.caption(_q)
                for _w in _warnings:
                    st.caption(f"ℹ️ {_w}")

            # 追加 assistant 消息到历史
            st.session_state.qa_messages.append({
                "role": "assistant",
                "content": _answer,
                "sources": _sources,
                "expanded_queries": _expanded,
            })

        # 检查 idle sessions（每次 rerun 都触发）
        if _pipeline_ok:
            try:
                _ended = _pipeline.check_idle_sessions()
                if _ended:
                    st.toast(f"Session ended after idle timeout.", icon="💾")
            except Exception:
                pass

    with profile_col:
        st.markdown('<div class="section-title" style="margin-top:0;">Learning Profile</div>',
                    unsafe_allow_html=True)

        if not _pipeline_ok:
            st.info("Set QWEN_API_KEY to enable the Study Assistant.")
        else:
            try:
                _prof = _pipeline.get_profile()

                # 薄弱点
                st.markdown('<div class="profile-section">'
                            '<div class="profile-label">Weak Points</div>', unsafe_allow_html=True)
                _wps = _prof.get("weak_points") or []
                if _wps:
                    for _wp in _wps[:6]:
                        st.markdown(
                            _profile_badge(_wp["concept"][:40], "#c0392b"),
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("None yet")
                st.markdown("</div>", unsafe_allow_html=True)

                # 已掌握
                st.markdown('<div class="profile-section">'
                            '<div class="profile-label">Mastered</div>', unsafe_allow_html=True)
                _mastered = _prof.get("mastered") or []
                if _mastered:
                    for _m in _mastered[:6]:
                        st.markdown(
                            _profile_badge(_m["concept"][:40], "#1a7a3c"),
                            unsafe_allow_html=True,
                        )
                else:
                    st.caption("None yet")
                st.markdown("</div>", unsafe_allow_html=True)

                # 学习风格
                _style = _prof.get("learning_style", "default")
                st.markdown(
                    f'<div class="profile-section">'
                    f'<div class="profile-label">Learning Style</div>'
                    f'{html.escape(_style)}</div>',
                    unsafe_allow_html=True,
                )

                # Session 控制
                st.markdown("---")
                st.caption(f"Session: `{st.session_state.qa_session_id}`")
                st.caption(f"Messages: {len(st.session_state.qa_messages)}")
                if st.button("End Session & Update Profile", use_container_width=True):
                    with st.spinner("Analyzing session..."):
                        try:
                            _end_result = _pipeline.end_session(st.session_state.qa_session_id)
                            import uuid as _uuid2
                            st.session_state.qa_session_id = _uuid2.uuid4().hex[:8]
                            st.session_state.qa_messages = []
                            _patch = _end_result.get("patch_result", {})
                            _applied = _patch.get("applied", "0 changes")
                            _details = _patch.get("details", [])
                            st.success(f"Profile updated: {_applied}")
                            if _details:
                                for _d in _details:
                                    st.caption(_d)
                        except Exception as _exc:
                            st.error(f"Session end failed: {_exc}")
                    st.rerun()

                if st.button("Clear Chat", use_container_width=True):
                    st.session_state.qa_messages = []
                    st.rerun()

            except Exception as _exc:
                st.error(f"Profile load failed: {_exc}")
