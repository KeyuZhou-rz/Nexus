"""Seven-day self-use numbers from local state: was Nexus used, and was it right?"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .academic_state import _as_float, _parse_due
from .academic_state_service import AcademicStateService
from .storage import load_tasks
from .usage_log import load_shown
from .v1_config import V1_TZ
from .workload import DEFAULT_EFFORT_HOURS, EFFORT_HOURS_BY_TAG

FOLLOW_WINDOW = timedelta(hours=24)


def _aware(value: Any) -> datetime | None:
    parsed = value if isinstance(value, datetime) else _parse_due(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=V1_TZ)


def _followed(shown_at: list[datetime], acted_at: list[datetime]) -> bool:
    return any(seen <= acted <= seen + FOLLOW_WINDOW for seen in shown_at for acted in acted_at)


def build_week(data_dir: Path, *, now: datetime | None = None, days: int = 7) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    current = (now or datetime.now(timezone.utc)).astimezone(V1_TZ)
    since = current - timedelta(days=days)
    store = AcademicStateService(root).store

    shown: dict[str, list[datetime]] = {}
    for entry in load_shown(root, since=since):
        shown.setdefault(entry["obligation_id"], []).append(entry["at"])

    acted: dict[str, list[datetime]] = {}
    completed: dict[str, datetime] = {}
    used_dates = {stamp.astimezone(V1_TZ).date() for stamps in shown.values() for stamp in stamps}
    for event in store.list_action_events(limit=10_000):
        at = _aware(event.get("occurred_at"))
        if at is None or at < since:
            continue
        oid = event["obligation_id"]
        used_dates.add(at.astimezone(V1_TZ).date())
        if event["event_type"] in {"started", "completed"}:
            acted.setdefault(oid, []).append(at)
        if event["event_type"] == "completed":
            completed[oid] = at

    without_timer = sum(
        1 for oid in completed
        if not any(e["event_type"] == "started" for e in store.list_action_events(obligation_id=oid, limit=10_000))
    )
    completed_recommended = sum(
        1 for oid, at in completed.items() if _followed(shown.get(oid, []), [at])
    )

    estimated = actual = 0.0
    measured = 0
    for sample in store.list_effort_samples():
        at = _aware(sample.get("completed_at"))
        if at is None or at < since or sample.get("source") != "timer":
            continue
        row = store.get_obligation(sample["obligation_id"]) or {}
        guess = _as_float(row.get("estimated_hours"))
        if guess is None:
            guess = EFFORT_HOURS_BY_TAG.get(str(row.get("kind") or ""), DEFAULT_EFFORT_HOURS)
        measured += 1
        estimated += guess
        actual += float(sample.get("minutes") or 0) / 60

    manual_added = 0
    for task in load_tasks(root / "tasks.json"):
        added = _aware(task.received_at)
        if task.source == "manual" and added is not None and added >= since:
            manual_added += 1

    return {
        "schema_version": "1.0",
        "since": since.isoformat(),
        "until": current.isoformat(),
        "days": days,
        "days_used": len(used_dates),
        "shown": len(shown),
        "followed": sum(1 for oid, stamps in shown.items() if _followed(stamps, acted.get(oid, []))),
        "completed": len(completed),
        "completed_recommended": completed_recommended,
        "completed_without_timer": without_timer,
        "manual_added": manual_added,
        "measured": measured,
        "estimated_hours": round(estimated, 1),
        "actual_hours": round(actual, 1),
    }
