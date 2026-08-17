"""Tonight plan — one suggested work window. Suggestion only; no calendar write."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .academic_state import is_obligation_candidate
from .intelligence.workload import (
    TYPE_NOUN,
    build_judgment,
    course_short,
    effort_key,
    resolve_effort,
)
from .models import Task
from .utils import localize_dt

_FALLBACK_HOURS = 1.0


@dataclass
class TonightPlan:
    available: bool
    task_id: str | None
    title: str | None
    course: str | None
    hours: float | None
    window_start: datetime | None
    window_end: datetime | None
    text: str | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "task_id": self.task_id,
            "title": self.title,
            "course": self.course,
            "hours": self.hours,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "text": self.text,
            "reason": self.reason,
        }


def build_tonight_plan(
    tasks: list[Task],
    *,
    now: datetime | None = None,
    completions: dict | None = None,
    focus_id: str | None = None,
    day_end_hour: int = 22,
) -> TonightPlan:
    """Suggest a single tonight work window for the focus obligation."""
    now_local = now or datetime.now().astimezone()
    done_ids = _done_ids(completions)
    focus, via_focus = _select_focus(tasks, now_local, completions, done_ids, focus_id)
    if focus is None:
        return _empty()

    hours, _basis = resolve_effort(focus, completions)
    start, end, late = _window(now_local, hours, day_end_hour)
    phrase = _item_phrase(focus)
    text = _plan_text(phrase, hours, start, end, late)
    reason = _plan_reason(focus, now_local, via_focus, late, start, end, hours, day_end_hour)
    return TonightPlan(
        available=True,
        task_id=focus.id,
        title=(focus.title or "").strip() or phrase,
        course=focus.course,
        hours=hours,
        window_start=start,
        window_end=end,
        text=text,
        reason=reason,
    )


def _empty() -> TonightPlan:
    return TonightPlan(
        available=False,
        task_id=None,
        title=None,
        course=None,
        hours=None,
        window_start=None,
        window_end=None,
        text=None,
        reason=None,
    )


def _select_focus(
    tasks: list[Task],
    now_local: datetime,
    completions: dict | None,
    done_ids: set[str],
    focus_id: str | None,
) -> tuple[Task | None, bool]:
    if focus_id:
        chosen = next((task for task in tasks if task.id == focus_id), None)
        if chosen is not None and _is_open(chosen, done_ids) and is_obligation_candidate(chosen):
            return chosen, True

    judgment = build_judgment(
        tasks,
        done_ids=done_ids,
        now=now_local,
        completions=completions,
    )
    if not judgment.anchor_task_id:
        return None, False
    chosen = next((task for task in tasks if task.id == judgment.anchor_task_id), None)
    if chosen is None or not _is_open(chosen, done_ids) or not is_obligation_candidate(chosen):
        return None, False
    return chosen, False


def _is_open(task: Task, done_ids: set[str]) -> bool:
    if task.id in done_ids:
        return False
    return getattr(task, "status", "open") != "done"


def _done_ids(completions: dict | None) -> set[str]:
    done: set[str] = set()
    for task_id, entry in (completions or {}).items():
        if isinstance(entry, dict) and entry.get("done_at"):
            done.add(str(task_id))
    return done


def _window(
    now_local: datetime,
    hours: float,
    day_end_hour: int,
) -> tuple[datetime, datetime, bool]:
    day_end = now_local.replace(hour=day_end_hour, minute=0, second=0, microsecond=0)
    start = _ceil_5min(now_local)
    if now_local >= day_end or start >= day_end:
        span = min(max(hours, 0.0), _FALLBACK_HOURS)
        if span <= 0:
            span = _FALLBACK_HOURS
        return start, start + timedelta(hours=span), True
    end = min(start + timedelta(hours=max(hours, 0.0)), day_end)
    if end <= start:
        span = min(max(hours, 0.0), _FALLBACK_HOURS) or _FALLBACK_HOURS
        return start, start + timedelta(hours=span), True
    return start, end, False


def _ceil_5min(value: datetime) -> datetime:
    trimmed = value.replace(second=0, microsecond=0)
    leftover = trimmed.minute % 5
    if leftover == 0 and value.second == 0 and value.microsecond == 0:
        return trimmed
    if leftover == 0 and (value.second or value.microsecond):
        return trimmed + timedelta(minutes=5)
    return trimmed + timedelta(minutes=5 - leftover)


def _plan_text(
    phrase: str,
    hours: float,
    start: datetime,
    end: datetime,
    late: bool,
) -> str:
    if late:
        return f"今晚剩下的时间先做第一部分：{phrase}（约 {hours:g}h）。"
    return (
        f"今晚 {_hhmm(start)}–{_hhmm(end)} 做 {phrase}的第一部分（约 {hours:g}h）。"
    )


def _plan_reason(
    task: Task,
    now_local: datetime,
    via_focus: bool,
    late: bool,
    start: datetime,
    end: datetime,
    hours: float,
    day_end_hour: int,
) -> str:
    due = localize_dt(task.due_at, now_local)
    if via_focus:
        item = "按你指定的焦点义务。"
    elif due is not None and due < now_local:
        item = "已截止，今晚优先处理。"
    else:
        item = "按判断选中的焦点义务。"
    if late:
        return item + "已过今晚结束时间，用剩余时间先做第一部分。"
    natural_end = start + timedelta(hours=max(hours, 0.0))
    if end < natural_end:
        return item + f"今晚窗口在 {day_end_hour:02d}:00 结束。"
    return item


def _item_phrase(task: Task) -> str:
    kind = TYPE_NOUN.get(effort_key(task), TYPE_NOUN["default"])
    course = course_short(task.course)
    if course:
        return f"{course} {kind}"
    title = (task.title or "").strip()
    return title[:32] if title else kind


def _hhmm(value: datetime) -> str:
    return value.strftime("%H:%M")
