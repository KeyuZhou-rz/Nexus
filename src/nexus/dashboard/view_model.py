from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from ..models import Task
from ..utils import localize_dt


@dataclass
class DashboardItem:
    title: str
    source: str
    course: str | None
    when_text: str
    bucket: str
    url: str | None
    snippet: str | None
    tags: list[str] = field(default_factory=list)


@dataclass
class DashboardGroup:
    label: str
    items: list[DashboardItem]


@dataclass
class Snapshot:
    total_tasks: int
    overdue: int
    due_today: int
    next_seven_days: int
    recent_updates: int


@dataclass
class DashboardState:
    generated_at: datetime
    greeting: str
    last_updated_text: str
    snapshot: Snapshot
    focus_items: list[DashboardItem]
    upcoming_groups: list[DashboardGroup]
    update_items: list[DashboardItem]
    briefing_lines: list[str]
    briefing_warnings: list[str]
    pipeline_ok: bool | None
    pipeline_summary: str
    pipeline_steps: list[dict[str, Any]]
    sync_sources: dict[str, dict[str, Any]]
    sync_receipt: dict[str, Any]
    learning_context: dict[str, Any]
    tasks_table: list[dict[str, str]]
    available_courses: list[str]
    available_sources: list[str]
    available_tags: list[str]


def _safe_read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_briefing_payload(path: Path) -> dict[str, Any]:
    data = _safe_read_json(path)
    return data if isinstance(data, dict) else {}


def load_pipeline_report(path: Path) -> dict[str, Any]:
    data = _safe_read_json(path)
    return data if isinstance(data, dict) else {}


def build_dashboard_state(
    tasks: list[Task],
    *,
    briefing_payload: dict[str, Any] | None = None,
    pipeline_report: dict[str, Any] | None = None,
    sync_sources: dict[str, dict[str, Any]] | None = None,
    sync_receipt: dict[str, Any] | None = None,
    learning_context: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    last_updated: datetime | None = None,
) -> DashboardState:
    now_local = (generated_at or datetime.now().astimezone())
    briefing_payload = briefing_payload or {}
    pipeline_report = pipeline_report or {}
    sync_sources = sync_sources or {}
    sync_receipt = sync_receipt or {}
    learning_context = learning_context or {}

    available_courses = sorted({task.course for task in tasks if task.course})
    available_sources = sorted({task.source for task in tasks if task.source})
    available_tags = sorted({tag for task in tasks for tag in task.tags})

    actionable = [task for task in tasks if task.due_at]
    updates = [task for task in tasks if _is_update_task(task)]
    updates.sort(key=lambda task: _sort_dt(task, now_local), reverse=True)

    overdue_tasks = [task for task in actionable if _localize(task.due_at, now_local) and _localize(task.due_at, now_local) < now_local]
    due_today_tasks = [task for task in actionable if _is_same_day(task.due_at, now_local)]
    next_seven_day_tasks = [
        task
        for task in actionable
        if _localize(task.due_at, now_local)
        and now_local <= _localize(task.due_at, now_local) <= now_local + timedelta(days=7)
    ]

    focus_candidates = sorted(
        actionable,
        key=lambda task: (_focus_rank(task, now_local), _sort_dt(task, now_local)),
    )
    focus_items = [_to_item(task, now_local) for task in focus_candidates[:6]]

    upcoming_groups = _build_upcoming_groups(tasks, now_local)
    update_items = [_to_item(task, now_local, bucket="update") for task in updates[:8]]

    pipeline_ok = pipeline_report.get("ok") if isinstance(pipeline_report.get("ok"), bool) else None
    pipeline_steps = pipeline_report.get("steps") if isinstance(pipeline_report.get("steps"), list) else []
    pipeline_summary = _pipeline_summary(pipeline_ok, pipeline_steps)

    snapshot = Snapshot(
        total_tasks=len(tasks),
        overdue=len(overdue_tasks),
        due_today=len(due_today_tasks),
        next_seven_days=len(next_seven_day_tasks),
        recent_updates=len([task for task in updates if _sort_dt(task, now_local) >= now_local - timedelta(days=2)]),
    )

    tasks_table = [_to_table_row(task, now_local) for task in sorted(tasks, key=lambda task: _sort_dt(task, now_local))]

    return DashboardState(
        generated_at=now_local,
        greeting=_greeting(now_local),
        last_updated_text=_relative_update_text(last_updated, now_local),
        snapshot=snapshot,
        focus_items=focus_items,
        upcoming_groups=upcoming_groups,
        update_items=update_items,
        briefing_lines=_briefing_lines(briefing_payload),
        briefing_warnings=list(briefing_payload.get("warnings") or []),
        pipeline_ok=pipeline_ok,
        pipeline_summary=pipeline_summary,
        pipeline_steps=pipeline_steps,
        sync_sources=sync_sources,
        sync_receipt=sync_receipt,
        learning_context=learning_context,
        tasks_table=tasks_table,
        available_courses=available_courses,
        available_sources=available_sources,
        available_tags=available_tags,
    )


def render_task_card(item: DashboardItem) -> str:
    title = escape(item.title)
    source = escape(item.source)
    course = escape(item.course) if item.course else "No course"
    when_text = escape(item.when_text)
    snippet = escape(item.snippet) if item.snippet else ""
    link = ""
    if item.url:
        safe_url = escape(item.url)
        link = f'<a class="task-link" href="{safe_url}" target="_blank">Open source</a>'
    snippet_html = f'<div class="task-snippet">{snippet}</div>' if snippet else ""
    return (
        f'<div class="task-card {escape(item.bucket)}">'
        f'<div class="task-title">{title}</div>'
        f'<div class="task-meta">{when_text} · {course} · {source}</div>'
        f'{snippet_html}'
        f"{link}"
        "</div>"
    )


def render_learning_focus_card(item: dict[str, Any]) -> str:
    name = escape(str(item.get("name") or "Unknown concept"))
    course = escape(str(item.get("course_id") or "Unassigned course"))
    urgency = escape(str(item.get("urgency") or "review").replace("_", " ").title())
    mastery = item.get("mastery")
    mastery_text = f"{float(mastery):.0%} mastery" if isinstance(mastery, (int, float)) else "Mastery not measured"
    source = escape(str(item.get("evidence_source") or "No evidence source"))
    trust = escape(str(item.get("trust_status") or "unclassified").replace("_", " "))
    related = item.get("related_task") if isinstance(item.get("related_task"), dict) else None
    if related:
        task_title = escape(str(related.get("title") or "upcoming task"))
        action = f'<div class="focus-action">Review before: {task_title}</div>'
    else:
        action = '<div class="focus-action muted">No upcoming task linked to this course.</div>'
    return (
        '<div class="learning-card">'
        f'<div class="learning-card-head"><div class="learning-title">{name}</div>'
        f'<div class="urgency-tag">{urgency}</div></div>'
        f'<div class="task-meta">{course} · {escape(mastery_text)}</div>'
        f'{action}'
        f'<div class="evidence-line">Evidence: {source} · {trust}</div>'
        '</div>'
    )


def _pipeline_summary(pipeline_ok: bool | None, steps: list[dict[str, Any]]) -> str:
    if pipeline_ok is True:
        return "Latest pipeline run completed successfully."
    if pipeline_ok is False:
        failed = [step.get("name", "unknown") for step in steps if not step.get("ok", False)]
        if failed:
            return "Pipeline needs attention: " + ", ".join(str(name) for name in failed)
        return "Pipeline reported a failure."
    return "No pipeline report found yet."


def _briefing_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("todo", "schedule"):
        items = payload.get(key) or []
        for item in items[:3]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text_en") or item.get("text_zh") or "").strip()
            if text:
                lines.append(text)
    return lines[:4]


def _build_upcoming_groups(tasks: list[Task], now_local: datetime) -> list[DashboardGroup]:
    groups: dict[str, list[DashboardItem]] = {
        "Overdue": [],
        "Today": [],
        "Tomorrow": [],
        "Next 7 Days": [],
        "Later": [],
    }
    for task in sorted([task for task in tasks if task.due_at], key=lambda task: _sort_dt(task, now_local)):
        due = _localize(task.due_at, now_local)
        if due is None:
            continue
        if due < now_local:
            label = "Overdue"
            bucket = "overdue"
        elif due.date() == now_local.date():
            label = "Today"
            bucket = "today"
        elif due.date() == (now_local.date() + timedelta(days=1)):
            label = "Tomorrow"
            bucket = "today"
        elif due <= now_local + timedelta(days=7):
            label = "Next 7 Days"
            bucket = "upcoming"
        else:
            label = "Later"
            bucket = "upcoming"
        groups[label].append(_to_item(task, now_local, bucket=bucket))

    return [DashboardGroup(label=label, items=items) for label, items in groups.items() if items]


def _to_item(task: Task, now_local: datetime, bucket: str | None = None) -> DashboardItem:
    return DashboardItem(
        title=task.title,
        source=task.source,
        course=task.course,
        when_text=_when_text(task, now_local),
        bucket=bucket or _task_bucket(task, now_local),
        url=task.url,
        snippet=task.snippet,
        tags=list(task.tags),
    )


def _to_table_row(task: Task, now_local: datetime) -> dict[str, str]:
    return {
        "title": task.title,
        "course": task.course or "",
        "source": task.source,
        "when": _when_text(task, now_local),
        "status": task.status,
        "tags": ", ".join(task.tags),
    }


def _focus_rank(task: Task, now_local: datetime) -> tuple[int, int]:
    due = _localize(task.due_at, now_local)
    if due is None:
        return (5, 0)
    if due < now_local:
        return (0, int(due.timestamp()))
    if due.date() == now_local.date():
        return (1, int(due.timestamp()))
    if due.date() == now_local.date() + timedelta(days=1):
        return (2, int(due.timestamp()))
    if due <= now_local + timedelta(days=7):
        return (3, int(due.timestamp()))
    return (4, int(due.timestamp()))


def _task_bucket(task: Task, now_local: datetime) -> str:
    if _is_update_task(task):
        return "update"
    due = _localize(task.due_at, now_local)
    if due is None:
        return "upcoming"
    if due < now_local:
        return "overdue"
    if due.date() == now_local.date() or due.date() == now_local.date() + timedelta(days=1):
        return "today"
    return "upcoming"


def _when_text(task: Task, now_local: datetime) -> str:
    target = _localize(task.due_at or task.received_at, now_local)
    if target is None:
        return "No time"
    delta_days = (target.date() - now_local.date()).days
    prefix = target.strftime("%b %d %H:%M")
    if delta_days < 0:
        return f"Overdue · {prefix}"
    if delta_days == 0:
        return f"Today · {prefix}"
    if delta_days == 1:
        return f"Tomorrow · {prefix}"
    if delta_days <= 7:
        return f"In {delta_days} days · {prefix}"
    return prefix


def _greeting(now_local: datetime) -> str:
    hour = now_local.hour
    if hour < 6:
        return "Night shift dashboard"
    if hour < 12:
        return "Morning dashboard"
    if hour < 18:
        return "Afternoon dashboard"
    return "Evening dashboard"


def _relative_update_text(last_updated: datetime | None, now_local: datetime) -> str:
    if last_updated is None:
        return "No synced task snapshot yet"
    delta = now_local - last_updated
    if delta < timedelta(minutes=1):
        return "Updated just now"
    if delta < timedelta(hours=1):
        return f"Updated {max(1, int(delta.total_seconds() // 60))}m ago"
    if delta < timedelta(days=1):
        return f"Updated {max(1, int(delta.total_seconds() // 3600))}h ago"
    return "Updated on " + last_updated.strftime("%Y-%m-%d %H:%M")


def _is_same_day(value: datetime | None, now_local: datetime) -> bool:
    local = _localize(value, now_local)
    return bool(local and local.date() == now_local.date())


def _sort_dt(task: Task, now_local: datetime) -> datetime:
    localized = _localize(task.due_at or task.received_at, now_local)
    if localized is not None:
        return localized
    return now_local + timedelta(days=3650)


def _is_update_task(task: Task) -> bool:
    tags = set(task.tags or [])
    if task.source == "gmail" and "course_notification" in tags:
        return True
    if task.source == "brightspace" and "announcement" in tags:
        return True
    if "grade" in tags:
        return True
    title = (task.title or "").lower()
    return title.startswith("[grade]")


_localize = localize_dt
