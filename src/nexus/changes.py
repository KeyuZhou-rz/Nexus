"""Inbox / Changes — classify obligation change log for student decisions.

v1 kinds are iCal due change / new assignment / disappeared. Created items
are already ingested; only due_changed and disappeared need a student choice.
Gmail is not an obligation source. Course-like mail can appear as a change.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .core.academic_state import recent_changes
from .core.io_utils import atomic_write_json
from .core.models import Task

ACK_FILENAME = "inbox_ack.json"

_NEEDS_DECISION = frozenset({"due_changed", "disappeared"})
_MAIL_SIGNAL = re.compile(
    r"(assignment|homework|exam|midterm|final|deadline|due date|syllabus|"
    r"visa|equipment.{0,24}due|截止|作业|考试|期中|期末)",
    re.IGNORECASE,
)
_MAIL_NOISE = re.compile(
    r"(newsletter|hiring|we are hiring|nvidia|gartner|wolfram|"
    r"book of the year|loan receipt|return receipt|developer news|"
    r"career development newsletter|see what's trending)",
    re.IGNORECASE,
)


@dataclass
class ChangeItem:
    kind: str
    title: str
    detail: str | None
    text: str
    needs_decision: bool
    obligation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "text": self.text,
            "needs_decision": self.needs_decision,
            "obligation_id": self.obligation_id,
        }


def list_inbox_changes(
    data_dir: Path,
    *,
    limit: int = 8,
    decisions_only: bool = False,
    tasks: Iterable[Task] | None = None,
) -> list[ChangeItem]:
    """Read obligation_changes and classify which ones need a student decision."""
    items: list[ChangeItem] = []
    for row in recent_changes(data_dir, limit=limit):
        kind = str(row.get("kind") or "")
        detail_raw = row.get("detail")
        oid_raw = row.get("obligation_id")
        item = ChangeItem(
            kind=kind,
            title=str(row.get("title") or ""),
            detail=None if detail_raw is None else str(detail_raw),
            text=str(row.get("text") or ""),
            needs_decision=kind in _NEEDS_DECISION,
            obligation_id=None if not oid_raw else str(oid_raw),
        )
        if decisions_only and not item.needs_decision:
            continue
        items.append(item)
    if tasks is not None:
        for item in mail_change_items(tasks, limit=limit):
            if decisions_only and not item.needs_decision:
                continue
            items.append(item)
    acked = load_acknowledged_keys(data_dir)
    unique: list[ChangeItem] = []
    seen: set[str] = set()
    for item in items:
        key = change_key(item)
        if key in acked or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:limit]


def change_key(item: ChangeItem) -> str:
    return f"{item.kind}:{item.obligation_id or item.title}"


def load_acknowledged_keys(data_dir: Path) -> set[str]:
    path = Path(data_dir).expanduser() / ACK_FILENAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return set()
    keys = data.get("keys") if isinstance(data, dict) else data
    if not isinstance(keys, list):
        return set()
    return {str(item) for item in keys if str(item).strip()}


def acknowledge_change(data_dir: Path, key: str) -> None:
    root = Path(data_dir).expanduser()
    keys = load_acknowledged_keys(root)
    keys.add(key)
    atomic_write_json(root / ACK_FILENAME, {"keys": sorted(keys)})


def mail_change_items(tasks: Iterable[Task], *, limit: int = 5) -> list[ChangeItem]:
    """Course-like Gmail only. Newsletters and receipts stay out of Tonight."""
    items: list[ChangeItem] = []
    for task in tasks:
        classified = _classify_mail(task)
        if classified is None:
            continue
        items.append(classified)
        if len(items) >= limit:
            break
    return items


def _classify_mail(task: Task) -> ChangeItem | None:
    if task.source != "gmail":
        return None
    tags = {str(tag).lower() for tag in (task.tags or [])}
    title = (task.title or "").strip()
    blob = f"{title} {task.snippet or ''}"
    if _MAIL_NOISE.search(blob):
        return None
    tagged = bool(tags & {"course_notification", "announcement"})
    if not tagged and not _MAIL_SIGNAL.search(blob):
        return None
    short = title[:48] + ("…" if len(title) > 48 else "")
    needs_decision = not re.search(r"\bvisa\b|has been received", title, re.IGNORECASE)
    return ChangeItem(
        kind="mail",
        title=title,
        detail=task.course,
        text=f"邮件：{short}",
        needs_decision=needs_decision,
    )
