"""Rule-based next action — the "what do I do tonight" layer.

Uses coarse per-type effort estimates until real completion history exists.
Decisioning is a pure function: no network, no LLM. Persistence is optional
and happens at the CLI / sync boundary, not inside ``build_judgment``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json
from .models import Task
from .utils import localize_dt

NEXT_ACTION_SCHEMA_VERSION = "1.0"
JUDGMENT_FILENAME = "judgment.json"

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

ACTION_TAGS = {
    "assignment",
    "homework",
    "lab",
    "exam",
    "quiz",
    "project",
    "deadline",
    "presentation",
    "midterm",
    "final",
    "work_block",
}
NOISE_TAGS = {"announcement", "grade", "email", "unread", "general", "meeting"}
LMS_SOURCES = {"brightspace", "canvas"}
TITLE_ACTION_RE = re.compile(
    r"(\bhomework\b|\bassignment\b|\blab\b|\bexam\b|\bquiz\b|\bmidterm\b|"
    r"\bfinal\b|\bproject\b|\bdue\b|作业|实验|考试|测验|期中|期末|截止|项目)",
    re.IGNORECASE,
)
HOLIDAY_RE = re.compile(
    r"(holiday|valentine|festival|solstice|equinox|new year|christmas|假日|节日|情人节)",
    re.IGNORECASE,
)

_WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TYPE_NOUN = {
    "assignment": "作业",
    "homework": "作业",
    "lab": "Lab",
    "exam": "exam",
    "midterm": "exam",
    "final": "exam",
    "project": "project",
    "quiz": "quiz",
    "presentation": "presentation",
    "default": "任务",
}
PACE_WINDOW_DAYS = 14


@dataclass
class WeekItem:
    """One remaining week obligation the student can start from Tonight."""

    task_id: str
    label: str
    title: str
    effort_hours: float | None = None
    recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "label": self.label,
            "title": self.title,
            "effort_hours": self.effort_hours,
            "recommended": self.recommended,
        }


@dataclass
class Judgment:
    """Canonical next-action contract (also persisted as ``judgment.json``)."""

    deadline: str | None
    upcoming: list[str] = field(default_factory=list)
    week_items: list[WeekItem] = field(default_factory=list)
    forecast: str | None = None
    suggestion: str | None = None
    first_step: str | None = None
    why: list[str] = field(default_factory=list)
    title: str | None = None
    due_at: datetime | None = None
    generated_at: datetime | None = None
    anchor_task_id: str | None = None
    overloaded: bool = False
    effort_hours: float | None = None
    effort_basis: str | None = None
    pace: str | None = None
    course: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.anchor_task_id and self.suggestion)

    def to_dict(self) -> dict[str, Any]:
        generated = self.generated_at or datetime.now(timezone.utc)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        due_iso = None
        if self.due_at is not None:
            due = self.due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            due_iso = due.isoformat()
        return {
            "schema_version": NEXT_ACTION_SCHEMA_VERSION,
            "available": self.available,
            "task_id": self.anchor_task_id,
            "title": self.title,
            "due_at": due_iso,
            "deadline": self.deadline,
            "suggestion": self.suggestion,
            "first_step": self.first_step,
            "forecast": self.forecast,
            "upcoming": list(self.upcoming),
            "week_items": [item.to_dict() for item in self.week_items],
            "why": list(self.why),
            "overloaded": self.overloaded,
            "effort_hours": self.effort_hours,
            "effort_basis": self.effort_basis,
            "pace": self.pace,
            "course": self.course,
            "generated_at": generated.isoformat(),
        }


# Public alias used by context / CLI / dashboard.
NextAction = Judgment


def is_actionable_deadline(task: Task) -> bool:
    """True when the task is something the student must produce or submit."""
    tags = {str(tag).lower() for tag in (task.tags or [])}
    if tags & NOISE_TAGS:
        return False
    if "work_block" in tags:
        return False
    title = task.title or ""
    course = task.course or ""
    if HOLIDAY_RE.search(title) or HOLIDAY_RE.search(course):
        return False
    if tags & ACTION_TAGS:
        return True
    if task.source in LMS_SOURCES:
        return True
    if task.source == "manual":
        return True
    return bool(TITLE_ACTION_RE.search(title))


def effort_key(task: Task) -> str:
    """Stable type key used to learn effort from past completions."""
    ordered = sorted(EFFORT_HOURS_BY_TAG.items(), key=lambda kv: -len(kv[0]))
    tags = {t.lower() for t in (task.tags or [])}
    for tag, _hours in ordered:
        if tag in tags:
            return tag
    title = task.title or ""
    for tag, _hours in ordered:
        if re.search(rf"\b{re.escape(tag)}\b", title, flags=re.IGNORECASE):
            return tag
        if tag == "homework" and "作业" in title:
            return tag
        if tag == "lab" and "实验" in title:
            return tag
        if tag == "exam" and ("考试" in title or "期中" in title or "期末" in title):
            return tag
    return "default"


def _effort_samples(completions: dict[str, Any] | None, key: str) -> list[float]:
    if not completions or not key:
        return []
    samples: list[float] = []
    for entry in completions.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("effort_key") or "") != key:
            continue
        minutes = entry.get("effort_minutes")
        if isinstance(minutes, (int, float)) and minutes > 0:
            samples.append(float(minutes) / 60.0)
    return samples


def learned_effort_hours(
    completions: dict[str, Any] | None,
    key: str,
    *,
    min_samples: int = 1,
) -> float | None:
    samples = _effort_samples(completions, key)
    if len(samples) < min_samples:
        return None
    samples.sort()
    mid = len(samples) // 2
    median = samples[mid] if len(samples) % 2 else (samples[mid - 1] + samples[mid]) / 2
    prior = EFFORT_HOURS_BY_TAG.get(key, DEFAULT_EFFORT_HOURS)
    n = len(samples)
    # Few samples shrink toward the type prior so one 8h outlier is not 1.35× forever.
    blended = (n * median + 2 * prior) / (n + 2)
    hours = blended if n < 3 else median
    return max(0.25, min(hours, 16.0))


def estimate_effort_hours(
    task: Task,
    completions: dict[str, Any] | None = None,
) -> float:
    learned = learned_effort_hours(completions, effort_key(task))
    if learned is not None:
        return learned
    key = effort_key(task)
    if key in EFFORT_HOURS_BY_TAG:
        return EFFORT_HOURS_BY_TAG[key]
    return DEFAULT_EFFORT_HOURS


def resolve_effort(
    task: Task,
    completions: dict[str, Any] | None = None,
) -> tuple[float, str]:
    key = effort_key(task)
    learned = learned_effort_hours(completions, key)
    if learned is not None:
        return learned, "history"
    if key in EFFORT_HOURS_BY_TAG:
        return EFFORT_HOURS_BY_TAG[key], "type"
    return DEFAULT_EFFORT_HOURS, "type"


def build_judgment(
    tasks: list[Task],
    done_ids: set[str] | None = None,
    now: datetime | None = None,
    learning_focus: list[dict[str, Any]] | None = None,
    completions: dict[str, Any] | None = None,
) -> Judgment:
    done = done_ids or set()
    now_local = now or datetime.now().astimezone()
    horizon = now_local + timedelta(days=JUDGMENT_HORIZON_DAYS)

    actionable = [
        (localize_dt(task.due_at, now_local), task)
        for task in tasks
        if task.id not in done
        and getattr(task, "status", "open") != "done"
        and localize_dt(task.due_at, now_local) is not None
        and is_actionable_deadline(task)
    ]
    if not actionable:
        return Judgment(deadline=None, generated_at=now_local)

    actionable.sort(key=lambda pair: pair[0])
    overdue = [(due, t) for due, t in actionable if due < now_local]
    nearest_due, nearest = overdue[0] if overdue else actionable[0]
    focus_due, focus = _pick_focus(actionable, overdue, now_local, completions)

    upcoming: list[str] = []
    week_items: list[WeekItem] = []
    for item_due, item in actionable:
        if item_due > horizon:
            continue
        hours, _basis = resolve_effort(item, completions)
        week_items.append(
            WeekItem(
                task_id=item.id,
                label=_week_line(item, item_due, now_local),
                title=(item.title or "").strip() or _item_phrase(item),
                effort_hours=hours,
                recommended=item.id == focus.id,
            )
        )
        if item.id != nearest.id and len(upcoming) < 3:
            upcoming.append(_week_line(item, item_due, now_local))
    week_items = [item for item in week_items if item.recommended] + [
        item for item in week_items if not item.recommended
    ]

    effort, effort_basis = resolve_effort(focus, completions)
    overloaded = (focus_due - now_local) < timedelta(hours=effort)
    headline = _headline(nearest, nearest_due, now_local)
    phrase = _item_phrase(focus)
    if effort_basis == "history":
        forecast = f"按照过去工作速度，{phrase}预计需要 {effort:g}h。"
    else:
        forecast = f"按同类任务估算，{phrase}预计需要 {effort:g}h。"

    if overdue:
        suggestion = f"今晚先清掉已截止的{_item_phrase(nearest)}。"
    elif overloaded:
        suggestion = f"今晚至少完成 {phrase} 的第一部分。"
    else:
        suggestion = f"今晚至少完成 {phrase} 的第一部分。"

    pace = _pace_line(focus, tasks, completions, now_local)
    if pace is None:
        for _item_due, item in actionable:
            if item.id == focus.id:
                continue
            pace = _pace_line(item, tasks, completions, now_local)
            if pace:
                break

    return Judgment(
        deadline=headline,
        upcoming=upcoming,
        week_items=week_items,
        forecast=forecast,
        suggestion=suggestion,
        first_step=suggestion,
        why=[],
        title=(focus.title or "").strip() or _item_phrase(focus),
        due_at=focus_due,
        generated_at=now_local,
        anchor_task_id=focus.id,
        overloaded=overloaded,
        effort_hours=effort,
        effort_basis=effort_basis,
        pace=pace,
        course=focus.course,
    )


def write_judgment(path: Path, judgment: Judgment) -> None:
    atomic_write_json(path, judgment.to_dict())


def judgment_path(data_dir: Path) -> Path:
    return Path(data_dir).expanduser() / JUDGMENT_FILENAME


def _pick_focus(
    actionable: list[tuple[datetime, Task]],
    overdue: list[tuple[datetime, Task]],
    now_local: datetime,
    completions: dict[str, Any] | None,
) -> tuple[datetime, Task]:
    if overdue:
        return overdue[0]
    today = [
        pair for pair in actionable
        if pair[0].date() == now_local.date()
    ]
    if today:
        return today[0]
    week = [
        pair for pair in actionable
        if pair[0] <= now_local + timedelta(days=JUDGMENT_HORIZON_DAYS)
    ]
    heavy: list[tuple[float, datetime, Task]] = []
    for due, task in week:
        hours, _basis = resolve_effort(task, completions)
        if hours < 5:
            continue
        slack = max((due - now_local).total_seconds() / 3600.0, 0.25)
        heavy.append((hours / slack, due, task))
    if heavy:
        heavy.sort(key=lambda item: item[0], reverse=True)
        return heavy[0][1], heavy[0][2]
    return actionable[0]


def course_short(course: str | None) -> str:
    raw = (course or "").strip()
    if not raw:
        return ""
    match = re.match(r"([A-Za-z]+)", raw)
    if match:
        return match.group(1)
    return raw.split()[0][:16]


def _item_phrase(task: Task) -> str:
    kind = TYPE_NOUN.get(effort_key(task), TYPE_NOUN["default"])
    course = course_short(task.course)
    if course:
        return f"{course} {kind}"
    title = (task.title or "").strip()
    return title[:32] if title else kind


def _headline(task: Task, due: datetime, now_local: datetime) -> str:
    phrase = _item_phrase(task)
    if due < now_local:
        return f"{phrase}已截止。"
    delta_days = (due.date() - now_local.date()).days
    if delta_days == 0:
        return f"你今天 {phrase}截止。"
    if delta_days == 1:
        return f"你明天 {phrase}截止。"
    return f"你{_weekday_cn(due)} {phrase}截止。"


def _week_line(task: Task, due: datetime, now_local: datetime) -> str:
    phrase = _item_phrase(task)
    if due < now_local:
        return f"{phrase} 已过期"
    return f"{_weekday_cn(due)} {phrase}"


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed


def course_finish_pace(
    course: str | None,
    tasks: list[Task],
    completions: dict[str, Any] | None,
    now_local: datetime,
    *,
    window_days: int = PACE_WINDOW_DAYS,
) -> tuple[float, int] | None:
    if not course or not completions:
        return None
    cutoff = now_local - timedelta(days=window_days)
    by_id = {task.id: task for task in tasks}
    leads: list[float] = []
    for task_id, entry in completions.items():
        if not isinstance(entry, dict) or not entry.get("done_at"):
            continue
        task = by_id.get(task_id)
        entry_course = entry.get("course") or (task.course if task else None)
        if str(entry_course or "") != course:
            continue
        done_at = _parse_iso(entry.get("done_at"))
        due_at = _parse_iso(entry.get("due_at"))
        if due_at is None and task is not None:
            due_at = localize_dt(task.due_at, now_local)
        if done_at is None or due_at is None:
            continue
        if done_at.tzinfo is None and now_local.tzinfo is not None:
            done_at = done_at.replace(tzinfo=now_local.tzinfo)
        if due_at.tzinfo is None and now_local.tzinfo is not None:
            due_at = due_at.replace(tzinfo=now_local.tzinfo)
        if done_at < cutoff:
            continue
        leads.append((due_at - done_at).total_seconds() / 3600.0)
    if not leads:
        return None
    return sum(leads) / len(leads), len(leads)


def _pace_line(
    task: Task,
    tasks: list[Task],
    completions: dict[str, Any] | None,
    now_local: datetime,
) -> str | None:
    stats = course_finish_pace(task.course, tasks, completions, now_local)
    if stats is None:
        return None
    avg, _count = stats
    course = course_short(task.course) or "这门课"
    kind = TYPE_NOUN.get(effort_key(task), "作业")
    if avg >= 0.5:
        return f"过去两周你的 {course} {kind}平均比截止时间提前 {avg:.0f} 小时完成。"
    if avg <= -0.5:
        return f"过去两周你的 {course} {kind}平均晚于截止 {abs(avg):.0f} 小时完成。"
    return f"过去两周你的 {course} {kind}基本卡着截止完成。"


def _task_label(task: Task) -> str:
    title = (task.title or "").strip()
    if len(title) > 48:
        title = title[:47] + "…"
    return title


def _weekday_cn(dt: datetime) -> str:
    return _WEEKDAYS_CN[dt.weekday()]
