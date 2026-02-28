from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Iterable

from ..config import AppConfig, default_config
from ..models import Task
from ..state_store import load_state
from ..knowledge.query import query_knowledge
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
    warm_message: str | None = None
    focus: list[BriefingItem] = field(default_factory=list)


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
    briefing: Briefing | None = None
    if use_llm and config.llm_enabled and client.is_configured():
        try:
            briefing = _llm_briefing(
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
    elif use_llm and config.llm_enabled:
        warnings.append("LLM not configured. Showing rule-based briefing only.")

    if briefing is None:
        briefing = _rule_briefing(filtered, task_index, now_local)
    briefing.warnings.extend(warnings)
    briefing.warm_message = _build_warm_message(briefing, now_local)
    briefing.focus = _select_focus_items(briefing, now_local, max_items=3)
    _inject_learning_context(briefing, now_local, config)
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


def select_window_tasks(
    tasks: Iterable[Task],
    window_days: int = 7,
    now: datetime | None = None,
    include_noise: bool = False,
) -> list[Task]:
    now_local = (now or datetime.now().astimezone())
    return _filter_tasks(list(tasks), now_local, window_days, include_noise=include_noise)


def _filter_tasks(
    tasks: list[Task],
    now_local: datetime,
    window_days: int,
    include_noise: bool = False,
) -> list[Task]:
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

        if not include_noise and _is_noise(task):
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
    _validate_llm_payload(data)

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


def _validate_llm_payload(data: object) -> None:
    if not isinstance(data, dict):
        raise LLMError("LLM output is not a JSON object.")
    if "todo" not in data or "schedule" not in data:
        raise LLMError("LLM output missing required keys.")
    if not isinstance(data.get("todo"), list) or not isinstance(data.get("schedule"), list):
        raise LLMError("LLM output has invalid list types.")


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


def _rule_briefing(
    tasks: list[Task],
    task_index: dict[str, Task],
    now_local: datetime,
) -> Briefing:
    todo: list[BriefingItem] = []
    schedule: list[BriefingItem] = []
    for task in tasks:
        title = _normalize_title(task)
        text_en, text_zh = _friendly_text(task, title, now_local)
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


def _friendly_text(task: Task, title: str, now_local: datetime) -> tuple[str, str]:
    due_at = _localize(task.due_at, now_local)
    if due_at:
        delta = due_at - now_local
        if delta.total_seconds() < 0:
            prefix_en = "Overdue"
            prefix_zh = "已过期"
        elif delta <= timedelta(hours=24):
            prefix_en = "Top priority"
            prefix_zh = "今天优先"
        elif delta <= timedelta(days=2):
            prefix_en = "Due soon"
            prefix_zh = "尽快安排"
        else:
            prefix_en = "On the schedule"
            prefix_zh = "日程安排"
        return f"{prefix_en}: {title}", f"{prefix_zh}：{title}"

    tags = set(task.tags or [])
    if task.source == "gmail":
        if "announcement" in tags:
            prefix_en, prefix_zh = "FYI", "通知"
        elif "course_notification" in tags:
            prefix_en, prefix_zh = "Course update", "课程提醒"
        else:
            prefix_en, prefix_zh = "Mail to check", "邮件提醒"
    elif task.source == "brightspace":
        prefix_en, prefix_zh = "Course update", "课程更新"
    elif task.source == "gcal":
        prefix_en, prefix_zh = "Calendar", "日程"
    else:
        prefix_en, prefix_zh = "To review", "待处理"
    return f"{prefix_en}: {title}", f"{prefix_zh}：{title}"


def _contains_cjk(text: str) -> bool:
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
    return False


def _build_warm_message(briefing: Briefing, now_local: datetime) -> str | None:
    total = len(briefing.todo) + len(briefing.schedule)
    if total == 0:
        return "Looks light today. Keep the momentum going."
    urgent = sum(
        1
        for item in briefing.schedule
        if item.due_at and item.due_at <= now_local + timedelta(hours=24)
    )
    hour = now_local.hour
    if 5 <= hour < 11:
        base = "Good morning."
    elif 11 <= hour < 15:
        base = "Good afternoon."
    elif 15 <= hour < 19:
        base = "Good afternoon."
    elif 19 <= hour < 23:
        base = "Good evening."
    else:
        base = "Still up late—take care of yourself."

    if urgent >= 2:
        return f"{base} It's a bit tight today—finish the top 1-2 items first."
    if urgent == 1:
        return f"{base} Start with the most urgent one and you'll be in good shape."
    if total >= 6:
        return f"{base} It's a full list—knock out two items and call it a win."
    return f"{base} You're on a good rhythm. Keep it steady."


def _select_focus_items(
    briefing: Briefing, now_local: datetime, max_items: int = 3
) -> list[BriefingItem]:
    if briefing.schedule:
        sorted_items = sorted(
            briefing.schedule,
            key=lambda item: item.due_at or now_local + timedelta(days=3650),
        )
        return sorted_items[:max_items]
    if briefing.todo:
        return briefing.todo[:max_items]
    return []


def _inject_learning_context(
    briefing: Briefing,
    now_local: datetime,
    config: AppConfig,
) -> None:
    """Augment briefing.todo with learner-state and knowledge retrieval hints."""
    state_path = config.data_dir / "state.json"
    weak_points: list[str] = []
    review_queue: list[str] = []
    low_mastery_topics: list[str] = []
    try:
        state = load_state(state_path)
        weak_points = [item.strip() for item in state.weak_points if str(item).strip()]
        review_queue = [item.strip() for item in state.review_queue if str(item).strip()]
        mastery = state.mastery if isinstance(state.mastery, dict) else {}
        low_mastery_topics = [
            str(topic).strip()
            for topic, score in mastery.items()
            if str(topic).strip() and isinstance(score, (int, float)) and float(score) <= 0.5
        ]
    except Exception as exc:  # pragma: no cover - runtime data guard
        briefing.warnings.append(f"State load failed: {exc}")

    context_topics: list[str] = []
    for topic in review_queue[:2]:
        if topic not in context_topics:
            context_topics.append(topic)
    for topic in low_mastery_topics[:2]:
        if topic not in context_topics:
            context_topics.append(topic)
    for topic in weak_points[:2]:
        if topic not in context_topics:
            context_topics.append(topic)

    for weak_point in context_topics[:3]:
        briefing.todo.append(
            BriefingItem(
                text_en=f"Review weak point: {weak_point}",
                text_zh=f"复习薄弱点：{weak_point}",
                due_at=None,
                source_ids=[],
                action_url=None,
            )
        )

    query_seed = context_topics[0] if context_topics else None
    if not query_seed and briefing.focus:
        query_seed = briefing.focus[0].text_en
    if not query_seed:
        return

    course_filter = None
    if briefing.focus and briefing.focus[0].source_ids:
        first_id = briefing.focus[0].source_ids[0]
        task = briefing.task_index.get(first_id)
        if task and task.course:
            course_filter = task.course

    try:
        summary = query_knowledge(
            Path(config.data_dir) / "chroma",
            query_text=query_seed,
            n_results=2,
            course_id=course_filter,
            doc_type=None,
        )
    except Exception as exc:  # pragma: no cover - optional module/runtime dependency
        briefing.warnings.append(f"Knowledge query unavailable: {exc}")
        return

    for item in summary.items[:2]:
        file_name = str(item.metadata.get("file_name", "knowledge"))
        snippet = _short_text(item.text, limit=100)
        briefing.todo.append(
            BriefingItem(
                text_en=f"Review note from {file_name}: {snippet}",
                text_zh=f"复习资料（{file_name}）：{snippet}",
                due_at=None,
                source_ids=[],
                action_url=None,
            )
        )


def _short_text(text: str, limit: int = 120) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."
