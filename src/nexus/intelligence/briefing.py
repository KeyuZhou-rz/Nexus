from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from typing import Iterable

from ..config import AppConfig, default_config
from ..models import Task
from .llm import LLMClient, LLMError, extract_json

BRIEFING_SCHEMA_VERSION = "1.0"

ACTION_KEYWORDS = [
    "submit",
    "due",
    "deadline",
    "exam",
    "quiz",
    "midterm",
    "final",
    "office hour",
    "meeting",
    "call",
    "rsvp",
    "presentation",
    "project",
    "assignment",
    "homework",
    "lab",
]

ACTION_KEYWORDS_ZH = [
    "提交",
    "截止",
    "考试",
    "测验",
    "期中",
    "期末",
    "答疑",
    "办公时间",
    "会议",
    "电话",
    "演讲",
    "项目",
    "作业",
    "实验",
]


@dataclass
class BriefingItem:
    text_en: str
    text_zh: str
    due_at: datetime | None
    source_ids: list[str]
    action_url: str | None


@dataclass
class Briefing:
    todo: list[BriefingItem]
    schedule: list[BriefingItem]
    warnings: list[str] = field(default_factory=list)
    task_index: dict[str, Task] = field(default_factory=dict)


def select_exam_reminders(tasks: Iterable[Task], window_days: int = 180) -> list[Task]:
    """Return exam-related tasks intended for LLM briefings only."""
    now = datetime.now()
    horizon = now + timedelta(days=window_days)
    selected: list[Task] = []
    for task in tasks:
        if "exam" not in task.tags:
            continue
        if task.due_at is None:
            continue
        due_at = task.due_at
        if due_at.tzinfo is None:
            if not (now <= due_at <= horizon):
                continue
        else:
            now_tz = datetime.now(tz=due_at.tzinfo)
            if not (now_tz <= due_at <= (now_tz + timedelta(days=window_days))):
                continue
        selected.append(task)
    return selected


def build_briefing(
    tasks: Iterable[Task],
    window_days: int = 7,
    now: datetime | None = None,
    config: AppConfig | None = None,
    use_llm: bool = True,
) -> Briefing:
    config = config or default_config()
    now_local = (now or datetime.now().astimezone())
    filtered = _filter_tasks(list(tasks), now_local, window_days)
    task_index = {task.id: task for task in filtered}

    warnings: list[str] = []
    client = LLMClient(config)
    if use_llm and client.is_configured():
        try:
            return _llm_briefing(
                filtered,
                task_index,
                now_local,
                window_days,
                client,
                use_json_output=config.llm_json_output,
            )
        except LLMError as exc:
            warnings.append(str(exc))
        except Exception as exc:  # pragma: no cover - safety net for UI
            warnings.append(f"LLM parse failed: {exc}")
    elif use_llm:
        warnings.append("LLM not configured. Showing rule-based briefing only.")

    briefing = _fallback_briefing(filtered, task_index, now_local)
    briefing.warnings.extend(warnings)
    return briefing


def briefing_payload(
    briefing: Briefing,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime | None = None,
) -> dict:
    generated = (generated_at or datetime.now().astimezone()).isoformat()
    return {
        "schema_version": BRIEFING_SCHEMA_VERSION,
        "generated_at": generated,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "todo": [_item_payload(item) for item in briefing.todo],
        "schedule": [_item_payload(item) for item in briefing.schedule],
        "warnings": briefing.warnings,
    }


def _item_payload(item: BriefingItem) -> dict[str, str | None | list[str]]:
    return {
        "text_en": item.text_en,
        "text_zh": item.text_zh,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "action_url": item.action_url,
        "source_ids": item.source_ids,
    }


def _filter_tasks(tasks: list[Task], now_local: datetime, window_days: int) -> list[Task]:
    window_end = now_local + timedelta(days=window_days)
    window_start = now_local
    email_min = now_local - timedelta(days=window_days)

    kept: list[Task] = []
    for task in tasks:
        due_at = _localize(task.due_at, now_local)
        received_at = _localize(task.received_at, now_local)

        if due_at:
            if due_at < window_start or due_at > window_end:
                continue
        else:
            if task.source == "gmail":
                if not received_at or received_at < email_min:
                    continue
            else:
                continue

        if _is_noise(task):
            continue

        kept.append(task)

    return sorted(kept, key=lambda t: _sort_key(t, now_local))


def _is_noise(task: Task) -> bool:
    text = f"{task.title} {task.snippet or ''}"
    if not _has_action_keywords(text) and "announcement" in task.tags:
        return True
    return False


def _has_action_keywords(text: str) -> bool:
    lowered = text.lower()
    for keyword in ACTION_KEYWORDS:
        if keyword in lowered:
            return True
    for keyword in ACTION_KEYWORDS_ZH:
        if keyword in text:
            return True
    return False


def _sort_key(task: Task, now_local: datetime) -> datetime:
    due_at = _localize(task.due_at, now_local)
    if due_at:
        return due_at
    received_at = _localize(task.received_at, now_local)
    if received_at:
        return received_at
    return now_local + timedelta(days=3650)


def _localize(value: datetime | None, now_local: datetime) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo:
        return value.astimezone(now_local.tzinfo)
    return value.replace(tzinfo=now_local.tzinfo)


def _llm_briefing(
    tasks: list[Task],
    task_index: dict[str, Task],
    now_local: datetime,
    window_days: int,
    client: LLMClient,
    use_json_output: bool = True,
) -> Briefing:
    window_end = now_local + timedelta(days=window_days)
    payload = {
        "schema_version": BRIEFING_SCHEMA_VERSION,
        "window_start": now_local.isoformat(),
        "window_end": window_end.isoformat(),
        "tasks": [_task_payload(task, now_local) for task in tasks],
        "output_schema": {
            "todo": [
                {
                    "text_en": "string",
                    "text_zh": "string",
                    "source_ids": ["task_id"],
                    "due_at": "ISO-8601 or null",
                    "action_url": "url or null",
                }
            ],
            "schedule": [
                {
                    "text_en": "string",
                    "text_zh": "string",
                    "source_ids": ["task_id"],
                    "due_at": "ISO-8601 or null",
                    "action_url": "url or null",
                }
            ],
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are the Nexus daily briefing assistant. Return JSON only. "
                "Create a bilingual briefing (English + Simplified Chinese). "
                "Do not add source labels (Gmail/Brightspace/Calendar). "
                "Do not invent times or details; use only provided data. "
                "For schedule items, provide due_at as ISO if available. "
                "Do not include time in the text; keep time in due_at. "
                "Keep texts short, lightly friendly, and action-oriented. "
                "Example JSON output: {\"todo\": [], \"schedule\": []}."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]

    response = client.chat(
        messages,
        temperature=0.2,
        response_format={"type": "json_object"} if use_json_output else None,
        max_tokens=1200,
    )
    data = extract_json(response.content)

    todo_items = _parse_items(data.get("todo", []), task_index, now_local)
    schedule_items = _parse_items(data.get("schedule", []), task_index, now_local)

    schedule_items.sort(key=lambda item: item.due_at or now_local)
    todo_items.sort(key=lambda item: item.due_at or now_local + timedelta(days=3650))

    return Briefing(
        todo=todo_items,
        schedule=schedule_items,
        warnings=[],
        task_index=task_index,
    )


def _task_payload(task: Task, now_local: datetime) -> dict[str, str | None | list[str]]:
    return {
        "id": task.id,
        "title": _normalize_title(task),
        "snippet": task.snippet,
        "due_at": _localize(task.due_at, now_local).isoformat() if task.due_at else None,
        "received_at": _localize(task.received_at, now_local).isoformat()
        if task.received_at
        else None,
        "url": task.url,
        "course": task.course,
        "tags": task.tags,
    }


def _parse_items(
    raw_items: Iterable[dict],
    task_index: dict[str, Task],
    now_local: datetime,
) -> list[BriefingItem]:
    items: list[BriefingItem] = []
    for raw in raw_items:
        text_en = str(raw.get("text_en", "")).strip()
        text_zh = str(raw.get("text_zh", "")).strip()
        raw_ids = raw.get("source_ids") or []
        if isinstance(raw_ids, str):
            raw_ids = [raw_ids]
        source_ids = _resolve_source_ids(raw_ids, raw.get("action_url"), task_index)

        due_at = None
        raw_due = raw.get("due_at")
        if raw_due:
            try:
                due_at = datetime.fromisoformat(str(raw_due))
                due_at = _localize(due_at, now_local)
            except ValueError:
                due_at = None
        if due_at is None and source_ids:
            due_at = _localize(task_index[source_ids[0]].due_at, now_local)

        action_url = raw.get("action_url")
        if not action_url and source_ids:
            action_url = task_index[source_ids[0]].url

        if not text_en and not text_zh:
            continue

        items.append(
            BriefingItem(
                text_en=text_en or text_zh,
                text_zh=text_zh or text_en,
                due_at=due_at,
                source_ids=source_ids,
                action_url=action_url,
            )
        )
    return items


def _resolve_source_ids(
    raw_ids: Iterable[str],
    action_url: str | None,
    task_index: dict[str, Task],
) -> list[str]:
    ids = [task_id for task_id in raw_ids if task_id in task_index]
    if ids:
        return ids
    if action_url:
        for task_id, task in task_index.items():
            if task.url == action_url:
                return [task_id]
    return []


def _fallback_briefing(
    tasks: list[Task],
    task_index: dict[str, Task],
    now_local: datetime,
) -> Briefing:
    todo: list[BriefingItem] = []
    schedule: list[BriefingItem] = []
    for task in tasks:
        title = _normalize_title(task)
        text_en, text_zh = _fallback_text(title, task.due_at is not None)
        item = BriefingItem(
            text_en=text_en,
            text_zh=text_zh,
            due_at=_localize(task.due_at, now_local),
            source_ids=[task.id],
            action_url=task.url,
        )
        if task.due_at:
            schedule.append(item)
        else:
            todo.append(item)

    schedule.sort(key=lambda item: item.due_at or now_local)
    todo.sort(key=lambda item: item.due_at or now_local + timedelta(days=3650))

    return Briefing(
        todo=todo,
        schedule=schedule,
        warnings=[],
        task_index=task_index,
    )


def _normalize_title(task: Task) -> str:
    title = task.title.strip()
    if task.course and task.course not in title:
        title = f"{title} ({task.course})"
    return title


def _fallback_text(title: str, has_time: bool) -> tuple[str, str]:
    if _contains_cjk(title):
        return title, title
    if has_time:
        return title, f"{title}（记得关注）"
    return f"Remember: {title}", f"记得处理：{title}"


def _contains_cjk(text: str) -> bool:
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False
