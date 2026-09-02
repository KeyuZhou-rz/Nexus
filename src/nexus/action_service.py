"""Obligation-keyed action events. The only V1 feedback write path."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .academic_state import _as_float, _parse_due, _parse_source_ids
from .academic_state_service import AcademicStateService, _due_iso, _resolve_now, _state_payload
from .storage import load_tasks, save_tasks
from .task_service import load_completions, save_completions
from .utils import dedupe_tasks, stable_id
from .v1_config import INACTIVE_OBLIGATION_STATUSES

_TERMINAL_EVENTS = frozenset({"stopped", "completed"})
_RUNNING_META = "running_obligation_id"
_RUNNING_STARTED_META = "running_started_at"


def _event_id(seed: str) -> str:
    return f"ae:{stable_id(seed)}"


def _sample_id(seed: str) -> str:
    return f"es:{stable_id(seed)}"


class ActionService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.state = AcademicStateService(self.data_dir)

    @property
    def store(self):
        return self.state.store

    def running_action(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        current = _resolve_now(now)
        oid = self.store.get_meta(_RUNNING_META)
        started_raw = self.store.get_meta(_RUNNING_STARTED_META)
        if not oid:
            oid, started_raw = self._infer_running()
        if not oid:
            return None
        row = self.store.get_obligation(oid)
        if row is None or str(row.get("status") or "") in INACTIVE_OBLIGATION_STATUSES:
            self._clear_running()
            return None
        started = _parse_due(started_raw)
        elapsed = None
        if started is not None:
            elapsed = max((current - started).total_seconds() / 60.0, 0.0)
        return {
            "obligation_id": oid,
            "title": row.get("title"),
            "started_at": started_raw,
            "elapsed_minutes": elapsed,
            "status": row.get("status"),
        }

    def accept(
        self,
        obligation_id: str,
        *,
        block_minutes: int | None = None,
        recommendation_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.record_event(
            obligation_id,
            "accepted",
            now=now,
            recommendation_key=recommendation_key,
            payload={"block_minutes": block_minutes},
        )

    def start(
        self,
        obligation_id: str,
        *,
        recommendation_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _resolve_now(now)
        running = self.running_action(now=current)
        if running and running["obligation_id"] == obligation_id:
            return running
        if running and running["obligation_id"] != obligation_id:
            raise ValueError("an action is already running")
        result = self.record_event(
            obligation_id,
            "started",
            now=current,
            recommendation_key=recommendation_key,
        )
        self.store.set_meta(_RUNNING_META, obligation_id)
        self.store.set_meta(_RUNNING_STARTED_META, current.isoformat())
        self._mirror_start(obligation_id, current)
        return result

    def stop(
        self,
        obligation_id: str,
        *,
        now: datetime | None = None,
        elapsed_minutes: float | None = None,
    ) -> dict[str, Any]:
        current = _resolve_now(now)
        minutes = elapsed_minutes
        if minutes is None:
            minutes = self._elapsed_minutes(obligation_id, current)
        result = self.record_event(
            obligation_id,
            "stopped",
            now=current,
            payload={"elapsed_minutes": minutes},
        )
        if minutes and minutes > 0:
            self._add_spent(obligation_id, minutes / 60.0, now=current, complete=False)
        self._clear_running_if(obligation_id)
        return result

    def complete(
        self,
        obligation_id: str,
        *,
        effort_minutes: float | None = None,
        recommendation_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _resolve_now(now)
        row = self._require(obligation_id)
        if str(row.get("status") or "") in INACTIVE_OBLIGATION_STATUSES:
            return {"obligation_id": obligation_id, "status": row.get("status"), "idempotent": True}
        minutes = effort_minutes
        if minutes is None:
            minutes = self._elapsed_minutes(obligation_id, current)
        result = self.record_event(
            obligation_id,
            "completed",
            now=current,
            recommendation_key=recommendation_key,
            payload={"effort_minutes": minutes},
        )
        spent_add = (minutes / 60.0) if minutes and minutes > 0 else 0.0
        self._write_status(
            obligation_id,
            status="done",
            progress_percent=100,
            remaining_hours=0.0,
            spent_add=spent_add,
            now=current,
            change_kind="completed",
        )
        if minutes and minutes > 0:
            key = f"{obligation_id}:effort:{current.isoformat()}"
            self.store.insert_effort_sample(
                sample_id=_sample_id(key),
                obligation_id=obligation_id,
                minutes=float(minutes),
                completed_at=current.isoformat(),
                source="timer" if effort_minutes is None else "manual",
                course_id=row.get("course_id"),
                kind=row.get("kind"),
                idempotency_key=key,
            )
        self._clear_running_if(obligation_id)
        self._mirror_complete(obligation_id, current, minutes)
        return result

    def defer(
        self,
        obligation_id: str,
        *,
        reason: str,
        next_review_at: datetime | str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.record_event(
            obligation_id,
            "deferred",
            now=now,
            payload={
                "reason": reason,
                "next_review_at": _due_iso(next_review_at),
            },
        )

    def record_event(
        self,
        obligation_id: str,
        event_type: str,
        *,
        now: datetime | None = None,
        payload: dict[str, Any] | None = None,
        recommendation_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        row = self._require(obligation_id)
        current = _resolve_now(now)
        occurred = current.isoformat()
        key = idempotency_key or f"{obligation_id}:{event_type}:{occurred}"
        inserted = self.store.insert_action_event(
            event_id=_event_id(key),
            obligation_id=obligation_id,
            event_type=event_type,
            occurred_at=occurred,
            payload_json=payload,
            recommendation_key=recommendation_key,
            idempotency_key=key,
        )
        if event_type == "started" and str(row.get("status") or "open") == "open":
            self._write_status(
                obligation_id,
                status="in_progress",
                now=current,
                change_kind="progress_changed",
            )
        return {
            "obligation_id": obligation_id,
            "event_type": event_type,
            "occurred_at": occurred,
            "inserted": inserted,
            "recommendation_key": recommendation_key,
            "payload": payload,
        }

    def _require(self, obligation_id: str) -> dict[str, Any]:
        row = self.store.get_obligation(obligation_id)
        if row is None:
            raise KeyError(obligation_id)
        return row

    def _write_status(
        self,
        obligation_id: str,
        *,
        status: str,
        now: datetime,
        progress_percent: Any = ...,
        remaining_hours: Any = ...,
        spent_add: float = 0.0,
        change_kind: str | None = None,
    ) -> None:
        row = self._require(obligation_id)
        spent = (_as_float(row.get("spent_hours")) or 0.0) + spent_add
        progress = row.get("progress_percent") if progress_percent is ... else progress_percent
        remaining = row.get("remaining_hours") if remaining_hours is ... else remaining_hours
        override = row.get("remaining_override_hours")
        if remaining_hours is not ...:
            override = remaining_hours
        title = str(row.get("title") or obligation_id)
        self.store.write_obligation_ingest(
            obligation_id=obligation_id,
            title=title,
            course=row.get("course"),
            kind=str(row.get("kind") or "assignment"),
            due_at=row.get("due_at"),
            status=status,
            source_ids=_parse_source_ids(row.get("source_ids")),
            primary_task_id=row.get("primary_task_id"),
            estimated_hours=row.get("estimated_hours"),
            spent_hours=spent,
            remaining_hours=remaining,
            risk_band=row.get("risk_band"),
            progress_percent=progress,
            remaining_override_hours=override,
            course_id=row.get("course_id"),
            next_step=row.get("next_step"),
            sources=[],
            change_kind=change_kind,
            change_detail=status,
            state_before=_state_payload(
                obligation_id=obligation_id,
                title=title,
                due_at=row.get("due_at") or None,
                status=str(row.get("status") or "open"),
                progress_percent=row.get("progress_percent"),
                remaining_hours=row.get("remaining_hours"),
            ),
            state_after=_state_payload(
                obligation_id=obligation_id,
                title=title,
                due_at=row.get("due_at") or None,
                status=status,
                progress_percent=progress,
                remaining_hours=remaining,
            ),
            now_iso=now.isoformat(),
            ingest_source="action",
        )

    def _add_spent(
        self,
        obligation_id: str,
        hours: float,
        *,
        now: datetime,
        complete: bool,
    ) -> None:
        row = self._require(obligation_id)
        spent = (_as_float(row.get("spent_hours")) or 0.0) + hours
        estimated = _as_float(row.get("estimated_hours")) or 0.0
        override = _as_float(row.get("remaining_override_hours"))
        if complete:
            remaining = 0.0
        elif override is not None:
            remaining = max(override - hours, 0.0)
        else:
            remaining = max(estimated - spent, 0.0)
        self._write_status(
            obligation_id,
            status="open" if not complete else "done",
            spent_add=hours,
            remaining_hours=remaining,
            now=now,
            change_kind="progress_changed",
        )

    def _elapsed_minutes(self, obligation_id: str, now: datetime) -> float | None:
        started_raw = None
        running_id = self.store.get_meta(_RUNNING_META)
        if running_id == obligation_id:
            started_raw = self.store.get_meta(_RUNNING_STARTED_META)
        if not started_raw:
            for event in self.store.list_action_events(obligation_id=obligation_id, limit=50):
                if event.get("event_type") in _TERMINAL_EVENTS:
                    break
                if event.get("event_type") == "started":
                    started_raw = event.get("occurred_at")
                    break
        started = _parse_due(started_raw)
        if started is None:
            return None
        elapsed = (now - started).total_seconds() / 60.0
        return round(elapsed) if elapsed > 0 else None

    def _infer_running(self) -> tuple[str | None, str | None]:
        seen_terminal: set[str] = set()
        for event in self.store.list_action_events(limit=200):
            oid = str(event.get("obligation_id") or "")
            kind = event.get("event_type")
            if kind in _TERMINAL_EVENTS:
                seen_terminal.add(oid)
            elif kind == "started" and oid not in seen_terminal:
                return oid, str(event.get("occurred_at") or "")
        return None, None

    def _clear_running(self) -> None:
        self.store.set_meta(_RUNNING_META, "")
        self.store.set_meta(_RUNNING_STARTED_META, "")

    def _clear_running_if(self, obligation_id: str) -> None:
        if self.store.get_meta(_RUNNING_META) == obligation_id:
            self._clear_running()

    def _mirror_start(self, obligation_id: str, now: datetime) -> None:
        row = self.store.get_obligation(obligation_id)
        task_id = (row or {}).get("primary_task_id")
        if not task_id:
            return
        completions = load_completions(self.data_dir)
        entry = completions.get(task_id)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("done_at") or entry.get("started_at"):
            return
        entry["started_at"] = now.isoformat()
        completions[str(task_id)] = entry
        save_completions(self.data_dir, completions)

    def _mirror_complete(
        self,
        obligation_id: str,
        now: datetime,
        minutes: float | None,
    ) -> None:
        row = self.store.get_obligation(obligation_id)
        if row is None:
            return
        task_id = row.get("primary_task_id")
        if not task_id:
            return
        completions = load_completions(self.data_dir)
        entry = completions.get(task_id)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("done_at"):
            return
        entry["done_at"] = now.isoformat()
        entry["effort_minutes"] = minutes
        if row.get("kind"):
            entry["effort_key"] = row.get("kind")
        if row.get("course"):
            entry["course"] = row.get("course")
        completions[str(task_id)] = entry
        save_completions(self.data_dir, completions)
        path = self.data_dir / "tasks.json"
        if path.is_file():
            tasks = load_tasks(path)
            for task in tasks:
                if task.id == task_id:
                    task.status = "done"
                    task.done_at = now
            save_tasks(dedupe_tasks(tasks), path)
