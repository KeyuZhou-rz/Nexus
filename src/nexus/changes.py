"""Inbox / Changes — classify obligation change log for student decisions.

v1 kinds are iCal due change / new assignment / disappeared. Created items
are already ingested; only due_changed and disappeared need a student choice.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .academic_state import recent_changes

_NEEDS_DECISION = frozenset({"due_changed", "disappeared"})


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
    return items
