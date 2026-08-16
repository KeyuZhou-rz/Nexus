"""Rule-based deadline judgment — the "what do I do tonight" layer.

Uses coarse per-type effort estimates until real completion history exists.
Designed to be safe without an LLM: pure functions, no network, no persistence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import Task
from ..utils import localize_dt

EFFORT_HOURS_BY_TAG: dict[str, float] = {
    "exam": 8.0,
    "midterm": 10.0,
    "final": 12.0,
    "project": 8.0,
    "assignment": 3.0,
    "homework": 3.0,
    "lab": 4.0,
    "quiz": 2.0,
    "reading": 1.0,
    "presentation": 5.0,
}
DEFAULT_EFFORT_HOURS = 2.0
JUDGMENT_HORIZON_DAYS = 7

_WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


@dataclass
class Judgment:
    deadline: str | None
    upcoming: list[str] = field(default_factory=list)
    forecast: str | None = None
    suggestion: str | None = None
    anchor_task_id: str | None = None
    overloaded: bool = False


def estimate_effort_hours(task: Task) -> float:
    # Longer tags first so "midterm"/"final" win over the shorter "exam".
    ordered = sorted(EFFORT_HOURS_BY_TAG.items(), key=lambda kv: -len(kv[0]))
    tags = {t.lower() for t in (task.tags or [])}
    for tag, hours in ordered:
        if tag in tags:
            return hours
    title = (task.title or "").lower()
    for tag, hours in ordered:
        if re.search(rf"\b{re.escape(tag)}\b", title):
            return hours
    return DEFAULT_EFFORT_HOURS


def build_judgment(
    tasks: list[Task],
    done_ids: set[str] | None = None,
    now: datetime | None = None,
) -> Judgment:
    done = done_ids or set()
    now_local = now or datetime.now().astimezone()
    horizon = now_local + timedelta(days=JUDGMENT_HORIZON_DAYS)

    actionable = [
        (localize_dt(task.due_at, now_local), task)
        for task in tasks
        if task.id not in done and localize_dt(task.due_at, now_local) is not None
    ]
    if not actionable:
        return Judgment(deadline=None)

    actionable.sort(key=lambda pair: pair[0])
    overdue = [(due, t) for due, t in actionable if due < now_local]
    due, anchor = overdue[0] if overdue else actionable[0]

    upcoming: list[str] = []
    for item_due, item in actionable:
        if item.id == anchor.id or item_due > horizon:
            continue
        if item_due < now_local:
            upcoming.append(f"{_task_label(item)} 已过期")
        else:
            upcoming.append(f"{_weekday_cn(item_due)} {_task_label(item)}")
        if len(upcoming) >= 3:
            break

    label = _task_label(anchor)
    effort = estimate_effort_hours(anchor)
    days_left = max(0, (due.date() - now_local.date()).days)
    if overdue:
        forecast = f"{label} 按类型估算 {effort:g}h，已过期"
    elif days_left == 0:
        forecast = f"{label} 按类型估算 {effort:g}h，今天截止"
    else:
        forecast = f"{label} 按类型估算 {effort:g}h，还剩 {days_left} 天（约 {effort / days_left:.1f}h/天）"

    overloaded = (due - now_local) < timedelta(hours=effort)
    if overdue:
        suggestion = f"已过期，今天优先完成 {label} 的第一步"
    elif overloaded:
        suggestion = f"今晚至少开始 {label}，先完成第一部分"
    else:
        suggestion = f"按节奏推进，先做 {label}"

    return Judgment(
        deadline=_deadline_line(anchor, due, now_local),
        upcoming=upcoming,
        forecast=forecast,
        suggestion=suggestion,
        anchor_task_id=anchor.id,
        overloaded=overloaded,
    )


def _task_label(task: Task) -> str:
    title = (task.title or "").strip()
    if len(title) > 48:
        title = title[:47] + "…"
    return title


def _weekday_cn(dt: datetime) -> str:
    return _WEEKDAYS_CN[dt.weekday()]


def _deadline_line(task: Task, due: datetime, now_local: datetime) -> str:
    label = _task_label(task)
    if due < now_local:
        return f"{label} 已截止"
    delta_days = (due.date() - now_local.date()).days
    if delta_days == 0:
        return f"{label} 今天截止"
    if delta_days == 1:
        return f"{label} 明天截止"
    return f"{_weekday_cn(due)} {label} 截止"
