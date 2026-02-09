from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta
import html
import re
import os
import importlib
import sys

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


def _load_briefing():
    try:
        module = importlib.import_module("nexus.intelligence.briefing")
        module = importlib.reload(module)
        build = getattr(module, "build_briefing", None)
        select_window = getattr(module, "select_window_tasks", None)
        if build is None:
            raise ImportError("build_briefing not found in nexus.intelligence.briefing")
        return build, select_window, None
    except Exception as exc:  # pragma: no cover - UI safety net
        return None, None, str(exc)



def _missing_ical_courses():
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
    if value is None:
        return None
    if value.tzinfo:
        return value.astimezone(now_local.tzinfo)
    return value.replace(tzinfo=now_local.tzinfo)


def _format_dt(value: datetime | None, now_local: datetime) -> str:
    local = _localize_dt(value, now_local)
    if not local:
        return "TBD"
    return local.strftime("%Y-%m-%d %H:%M %Z")

def _task_title(task) -> str:
    return task.title.strip()


def _is_course_reminder(task) -> bool:
    tags = set(task.tags or [])
    if task.source == "gmail":
        return "course_notification" in tags
    if task.source == "brightspace":
        return "announcement" in tags
    return False


def _display_name() -> str | None:
    name = os.getenv("NEXUS_DISPLAY_NAME", "").strip()
    return name or None


def _weekday_en(now_local: datetime) -> str:
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return weekdays[now_local.weekday()]


def _format_abs_date(value: datetime, now_local: datetime) -> str:
    local = _localize_dt(value, now_local)
    if not local:
        return "Unknown time"
    return local.strftime("%b %d %H:%M")


def _relative_due_text(value: datetime | None, now_local: datetime) -> str:
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
    if 23 <= hour or hour < 6:
        return "🌙 Late night — get some rest"
    if 6 <= hour < 12:
        return "☀️ Good morning"
    if 12 <= hour < 18:
        return "👋 Good afternoon"
    return "🌆 Good evening"


def _is_deadline_task(task) -> bool:
    tags = set(task.tags or [])
    if {"assignment", "exam", "deadline"} & tags:
        return True
    return False


def _count_deadlines_in_range(tasks, start: datetime, end: datetime, now_local: datetime) -> int:
    count = 0
    for task in tasks:
        if not _is_deadline_task(task):
            continue
        due = _localize_dt(task.due_at, now_local)
        if due and start <= due < end:
            count += 1
    return count


def _get_smart_tip(deadlines, now_local: datetime) -> str | None:
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
    tags = set(task.tags or [])
    title = (task.title or "").lower()
    if "holiday" in tags:
        return True
    holiday_markers = ("holiday", "festival", "break", "vacation", "节", "假期")
    return any(marker in title for marker in holiday_markers)


def _holiday_ranges(tasks, now_local: datetime) -> list[tuple[datetime.date, datetime.date]]:
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
    return _is_course_reminder(task)


def _is_meeting_task(task) -> bool:
    title = (task.title or "").lower()
    meeting_markers = ("meeting", "office hour", "office hours", "call", "sync", "standup", "会议", "答疑")
    return any(marker in title for marker in meeting_markers)


def _event_type(task) -> str:
    tags = set(task.tags or [])
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
    return {
        "assignment": "📝",
        "exam": "📄",
        "holiday": "🎉",
        "meeting": "👥",
        "course": "📬",
    }.get(event_type, "📌")


def _urgency_class(event_type: str, due_at: datetime | None, now_local: datetime) -> str:
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
[data-testid="stSidebar"] { background: #0e1420; }
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
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 12px 16px;
    margin-bottom: 28px;
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
.event-card {
    background: var(--panel-2);
    border: 1px solid var(--border);
    border-left: 4px solid #34C759;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 16px;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    animation: cardIn 0.35s ease both;
}
.event-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
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
.event-urgent-24h { border-left-color: #FF3B30; background: rgba(255, 59, 48, 0.06); }
.event-urgent-3day { border-left-color: #FF9500; background: rgba(255, 149, 0, 0.06); }
.event-urgent-future { border-left-color: #34C759; background: rgba(52, 199, 89, 0.06); }
.event-info { border-left-color: #007AFF; background: rgba(0, 122, 255, 0.06); }
.event-overdue { border-left-color: #FF3B30; background: rgba(255, 59, 48, 0.12); }
.event-exam { border-left-width: 6px; }
.event-meeting { border-style: dashed; }
.event-holiday { background: rgba(0, 122, 255, 0.12); }
.event-course { background: rgba(0, 122, 255, 0.08); }
@keyframes cardIn {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
}
div[data-testid="stExpander"] {
    border: 1px solid var(--border);
    border-radius: 12px;
    background: rgba(17, 24, 38, 0.55);
}
div[data-testid="stExpander"] > details {
    padding: 8px 10px;
}
div[data-testid="stExpander"] summary {
    font-weight: 600;
    color: var(--text);
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
_, select_window_tasks, briefing_error = _load_briefing()

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

action_tasks = [
    task
    for task in tasks
    if task.due_at
    and not _is_course_update(task)
    and (_localize_dt(task.due_at, now_local) or now_local) >= cutoff_time
]
action_tasks.sort(key=lambda t: _localize_dt(t.due_at, now_local) or now_local + timedelta(days=3650))

urgent: list = []
upcoming: list = []
later: list = []
for task in action_tasks:
    due = _localize_dt(task.due_at, now_local)
    if not due:
        continue
    if due <= now_local + timedelta(days=3):
        urgent.append(task)
    elif due <= now_local + timedelta(days=7):
        upcoming.append(task)
    else:
        later.append(task)

course_updates: list = []
course_cutoff = cutoff_time
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

left_panel, right_panel = st.columns([2, 3], gap="large")

with left_panel:
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

    tip_html = "".join(
        f'<div class="tip-line">{html.escape(line)}</div>'
        for line in [greeting, *tip_lines]
    )
    st.markdown(f'<div class="status-tips">{tip_html}</div>', unsafe_allow_html=True)

    with st.expander("System", expanded=False):
        if briefing_error:
            st.warning(f"Briefing module not available: {briefing_error}")
        if calendar_sync_error:
            st.warning(f"Calendar sync issue: {calendar_sync_error}")

        st.subheader("Aggregation")
        if st.button("Run Aggregation (Google + Brightspace)"):
            with st.spinner("Aggregating..."):
                result = run_aggregation(include_google=True)
            if result.errors:
                for err in result.errors:
                    st.error(err)
            st.success(f"Aggregated {len(result.tasks)} items.")

            window_days = 14
            if select_window_tasks is not None:
                display_tasks = select_window_tasks(
                    result.tasks, window_days=window_days, include_noise=True
                )
            else:
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

with right_panel:
    st.markdown(
        '<div class="section-title" style="margin-top: 0;">🔥 Needs Attention</div>',
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
        for idx, task in enumerate(upcoming):
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
