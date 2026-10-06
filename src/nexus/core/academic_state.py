"""Academic obligations — stable identity and the single write path.

Task snapshots stay a collector cache. Tonight will later read obligations.
due_at is an attribute, never part of the primary key, so a deadline move
keeps the same obligation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .workload import effort_key, is_actionable_deadline, resolve_effort
from .models import Task
from .utils import localize_dt, stable_id

_SOURCE_RANK = {
    "brightspace": 0,
    "canvas": 1,
    "manual": 2,
    "gcal": 3,
    "gmail": 4,
}
_SPACE = re.compile(r"[\s_]+")
_TITLE_JUNK = re.compile(r"[^\w\s一-鿿-]", flags=re.UNICODE)


@dataclass(frozen=True)
class Obligation:
    id: str
    title: str
    course: str | None
    kind: str
    due_at: datetime | None
    status: str
    source_ids: list[str]
    primary_task_id: str | None
    estimated_hours: float | None = None
    spent_hours: float | None = None
    remaining_hours: float | None = None
    risk_band: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "course": self.course,
            "kind": self.kind,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "status": self.status,
            "source_ids": list(self.source_ids),
            "primary_task_id": self.primary_task_id,
            "estimated_hours": self.estimated_hours,
            "spent_hours": self.spent_hours,
            "remaining_hours": self.remaining_hours,
            "risk_band": self.risk_band,
        }


def normalize_course(value: str | None) -> str:
    return _SPACE.sub(" ", (value or "").strip()).casefold()


def display_course(value: str | None) -> str:
    """Short label for LMS course dumps; identity still uses normalize_course."""
    raw = (value or "").strip()
    if not raw:
        return ""
    first = raw.split(",")[0].strip()
    first = re.sub(r"\s+\d{2,}[-_]\d+.*$", "", first).strip()
    bracket = re.search(r"《([^》]+)》", first)
    if bracket:
        first = bracket.group(1).strip()
    if len(first) > 40:
        return first[:39] + "…"
    return first


def normalize_title(value: str | None) -> str:
    text = _TITLE_JUNK.sub(" ", (value or "").strip().casefold())
    return _SPACE.sub(" ", text).strip()


def is_obligation_candidate(task: Task) -> bool:
    tags = {str(tag).lower() for tag in (task.tags or [])}
    if "work_block" in tags:
        return False
    return is_actionable_deadline(task)


def obligation_id_for_task(task: Task) -> str | None:
    """Stable key: kind + course + title. due_at is intentionally excluded."""
    if not is_obligation_candidate(task):
        return None
    if task.obligation_id:
        return task.obligation_id
    title = normalize_title(task.title)
    if not title:
        return None
    kind = effort_key(task)
    course = normalize_course(task.course)
    return "ob:" + stable_id(f"{kind}|{course}|{title}")


def persist_obligations(
    data_dir: Path,
    tasks: Iterable[Task],
    *,
    reconcile_missing: bool = False,
) -> dict[str, int]:
    """Upsert actionable tasks into SQLite. work_block / noise are skipped."""
    from .academic_state_service import AcademicStateService

    return AcademicStateService(data_dir).ingest_snapshot(
        tasks, reconcile_missing=reconcile_missing
    )


def refresh_from_snapshot(data_dir: Path, snapshot: list[Task]) -> list[Task]:
    """Bootstrap legacy snapshots once; subsequent page reads are projections."""
    from ..knowledge.sqlite_store import SQLiteStore

    store = SQLiteStore(Path(data_dir) / "nexus.db")
    if not store.list_obligations():
        persist_obligations(data_dir, snapshot)
    if store.get_meta("legacy_completions_imported_at") is None:
        from .academic_state_service import AcademicStateService

        AcademicStateService(data_dir).import_legacy_completions()
    return tasks_from_obligations(data_dir, snapshot)


def tasks_from_obligations(data_dir: Path, snapshot: list[Task]) -> list[Task]:
    """Project persisted obligations back onto Task objects for Tonight."""
    from ..knowledge.sqlite_store import SQLiteStore

    root = Path(data_dir).expanduser()
    db_path = root / "nexus.db"
    candidates = [task for task in snapshot if is_obligation_candidate(task)]
    if not db_path.is_file():
        return candidates
    store = SQLiteStore(db_path)
    rows = store.list_obligations(statuses=["open", "in_progress", "overdue"])
    by_id = {task.id: task for task in snapshot}
    projected: list[Task] = []
    for row in rows:
        source_ids = _parse_source_ids(row.get("source_ids"))
        primary_id = row.get("primary_task_id")
        base = None
        if primary_id and primary_id in by_id:
            base = by_id[primary_id]
        else:
            base = next((by_id[item] for item in source_ids if item in by_id), None)
        due_at = _parse_due(row.get("due_at"))
        hours = _hours_from_row(row)
        if base is None:
            projected.append(
                Task(
                    id=str(primary_id or row["id"]),
                    title=str(row.get("title") or ""),
                    due_at=due_at,
                    source="obligation",
                    course=row.get("course"),
                    status="open",
                    tags=[str(row.get("kind") or "assignment")],
                    obligation_id=row["id"],
                    **hours,
                )
            )
            continue
        projected.append(
            Task(
                id=base.id,
                title=str(row.get("title") or base.title),
                due_at=due_at if due_at is not None else base.due_at,
                source=base.source,
                url=base.url,
                course=row.get("course") or base.course,
                status=row["status"],
                priority=base.priority,
                tags=list(base.tags),
                snippet=base.snippet,
                received_at=base.received_at,
                obligation_id=row["id"],
                **hours,
            )
        )
    return projected


def recent_changes(data_dir: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    from ..knowledge.sqlite_store import SQLiteStore

    db_path = Path(data_dir).expanduser() / "nexus.db"
    if not db_path.is_file():
        return []
    store = SQLiteStore(db_path)
    titles = {row["id"]: row.get("title") for row in store.list_obligations()}
    lines: list[dict[str, Any]] = []
    for row in store.list_obligation_changes(limit=limit):
        title = titles.get(row["obligation_id"]) or row.get("detail") or ""
        lines.append(
            {
                "kind": row["kind"],
                "title": title,
                "detail": row.get("detail"),
                "text": _change_text(row["kind"], str(title or ""), row.get("detail")),
                "obligation_id": row.get("obligation_id"),
            }
        )
    return lines


def _change_text(kind: str, title: str, detail: Any) -> str:
    if kind == "due_changed":
        return f"{title} 截止 {detail}"
    if kind == "created":
        return f"新义务：{title or detail}"
    if kind == "disappeared":
        return f"已从日历消失：{title or detail}"
    return str(detail or title)


def _parse_source_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _hours_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "estimated_hours": _as_float(row.get("estimated_hours")),
        "spent_hours": _as_float(row.get("spent_hours")),
        "remaining_hours": _as_float(row.get("remaining_hours")),
        "risk_band": row.get("risk_band"),
    }


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_due(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _effort_snapshot(
    primary: Task,
    source_ids: list[str],
    completions: dict[str, dict],
    due_at: datetime | None,
    status: str,
    now_local: datetime,
) -> tuple[float, float, float, str]:
    estimated, _basis = resolve_effort(primary, completions)
    spent = _spent_hours(source_ids, completions, now_local)
    if status == "done":
        remaining = 0.0
    else:
        remaining = max(estimated - spent, 0.0)
    # No available-time input yet: never label HIGH.
    due_local = localize_dt(due_at, now_local) if due_at else None
    if status != "done" and due_local is not None and due_local < now_local:
        risk_band = "overdue"
    else:
        risk_band = "unscored"
    return round(estimated, 3), round(spent, 3), round(remaining, 3), risk_band


def _spent_hours(
    source_ids: list[str],
    completions: dict[str, dict],
    now_local: datetime,
) -> float:
    total_minutes = 0.0
    for task_id in source_ids:
        entry = completions.get(task_id)
        if not isinstance(entry, dict):
            continue
        minutes = entry.get("effort_minutes")
        if entry.get("done_at") and isinstance(minutes, (int, float)) and minutes > 0:
            total_minutes += float(minutes)
            continue
        started_raw = entry.get("started_at")
        if started_raw and not entry.get("done_at"):
            try:
                started = datetime.fromisoformat(str(started_raw))
            except ValueError:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=now_local.tzinfo)
            total_minutes += max((now_local - started).total_seconds() / 60, 0)
    return total_minutes / 60.0


def _source_rank(task: Task) -> tuple[int, str]:
    return (_SOURCE_RANK.get(task.source, 9), task.id or "")
