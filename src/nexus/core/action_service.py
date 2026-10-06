"""Atomic feedback against canonical obligation IDs."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .academic_state import _as_float, _parse_due, _parse_source_ids
from .academic_state_service import AcademicStateService, _resolve_now, _state_payload
from .db import connect
from .storage import load_tasks, save_tasks
from .task_service import load_completions, save_completions
from .utils import stable_id
from .v1_config import INACTIVE_OBLIGATION_STATUSES

_RUNNING_META = "running_obligation_id"
_RUNNING_STARTED_META = "running_started_at"


class ActionService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.state = AcademicStateService(self.data_dir)

    @property
    def store(self):
        return self.state.store

    def running_action(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        current = _resolve_now(now)
        with connect(self.store.db_path) as conn:
            oid, started_raw = self._running_state(conn)
        row = self.store.get_obligation(oid) if oid else None
        if row is None or row["status"] in INACTIVE_OBLIGATION_STATUSES:
            return None
        started = _parse_due(started_raw)
        return {
            "obligation_id": oid,
            "title": row["title"],
            "started_at": started_raw,
            "elapsed_minutes": (
                max((current - started).total_seconds() / 60, 0) if started else None
            ),
            "status": row["status"],
        }

    def _running_state(self, conn):
        oid = self.store.get_meta(_RUNNING_META, connection=conn)
        if oid:
            row = self.store.get_obligation(oid, connection=conn)
            started = self.store.get_meta(_RUNNING_STARTED_META, connection=conn)
            ended = conn.execute(
                """SELECT 1 FROM state_changes WHERE entity_id = ?
                   AND json_extract(after_json, '$.status') IN ('cancelled', 'missing', 'done')
                   AND julianday(created_at) >= julianday(?) LIMIT 1""",
                (oid, started),
            ).fetchone()
            if row and row["status"] not in INACTIVE_OBLIGATION_STATUSES and not ended:
                return oid, started
        row = conn.execute(
            """SELECT e.obligation_id, e.occurred_at FROM (
                   SELECT *, ROW_NUMBER() OVER (
                       PARTITION BY obligation_id ORDER BY julianday(occurred_at) DESC, rowid DESC
                   ) AS latest FROM action_events
                   WHERE event_type IN ('started', 'stopped', 'completed')
               ) e JOIN obligations o ON o.id = e.obligation_id
               WHERE e.latest = 1 AND e.event_type = 'started'
                 AND o.status IN ('open', 'in_progress', 'overdue')
                 AND NOT EXISTS (
                     SELECT 1 FROM state_changes s WHERE s.entity_id = e.obligation_id
                     AND json_extract(s.after_json, '$.status') IN ('cancelled', 'missing', 'done')
                     AND julianday(s.created_at) >= julianday(e.occurred_at)
                 )
               ORDER BY julianday(e.occurred_at) DESC LIMIT 1"""
        ).fetchone()
        return (row["obligation_id"], row["occurred_at"]) if row else (None, None)

    def accept(
        self, obligation_id, *, block_minutes=None, recommendation_key=None, now=None
    ):
        return self.record_event(
            obligation_id,
            "accepted",
            now=now,
            recommendation_key=recommendation_key,
            payload={"block_minutes": block_minutes},
        )

    def start(self, obligation_id, *, recommendation_key=None, now=None):
        return self.record_event(
            obligation_id, "started", now=now, recommendation_key=recommendation_key
        )

    def stop(self, obligation_id, *, now=None, elapsed_minutes=None):
        return self.record_event(
            obligation_id,
            "stopped",
            now=now,
            payload={"elapsed_minutes": elapsed_minutes},
        )

    def complete(
        self, obligation_id, *, effort_minutes=None, recommendation_key=None, now=None
    ):
        return self.record_event(
            obligation_id,
            "completed",
            now=now,
            recommendation_key=recommendation_key,
            payload={"effort_minutes": effort_minutes},
        )

    def defer(self, obligation_id, *, reason, next_review_at=None, now=None):
        current = _resolve_now(now)
        return self.record_event(
            obligation_id,
            "deferred",
            now=current,
            payload={
                "reason": reason,
                "next_review_at": (
                    (next_review_at or current + timedelta(hours=1)).isoformat()
                    if not isinstance(next_review_at, str)
                    else next_review_at
                ),
            },
        )

    def record_event(
        self,
        obligation_id,
        event_type,
        *,
        now=None,
        payload=None,
        recommendation_key=None,
        idempotency_key=None,
    ):
        if event_type not in {
            "accepted",
            "started",
            "stopped",
            "completed",
            "deferred",
            "resumed",
        }:
            raise ValueError("unsupported action")
        current = _resolve_now(now)
        stamp = current.isoformat()
        details = dict(payload or {})
        with connect(self.store.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.store.get_obligation(obligation_id, connection=conn)
            if row is None:
                raise KeyError(obligation_id)
            if row["status"] in INACTIVE_OBLIGATION_STATUSES:
                if event_type == "completed":
                    return {
                        "obligation_id": obligation_id,
                        "status": row["status"],
                        "idempotent": True,
                    }
                raise ValueError("work is already completed")
            running, started_raw = self._running_state(conn)
            if event_type == "started" and running:
                if running == obligation_id:
                    return {"obligation_id": obligation_id, "inserted": False}
                raise ValueError("an action is already running")
            if event_type == "stopped" and running != obligation_id:
                return {"obligation_id": obligation_id, "inserted": False}
            started = _parse_due(started_raw) if running == obligation_id else None
            elapsed = max((current - started).total_seconds() / 60, 0) if started else 0
            spent = _as_float(row.get("spent_hours")) or 0
            if event_type == "stopped":
                minutes = details.get("elapsed_minutes")
                minutes = elapsed if minutes is None else float(minutes)
                if minutes < 0:
                    raise ValueError("elapsed minutes must not be negative")
                details["elapsed_minutes"] = minutes
                spent += minutes / 60
            elif event_type == "completed":
                actual = details.get("effort_minutes")
                if actual is not None and float(actual) < 0:
                    raise ValueError("actual minutes must not be negative")
                spent = (
                    max(spent, float(actual) / 60)
                    if actual is not None
                    else spent + elapsed / 60
                )
                details["effort_minutes"] = spent * 60 if spent > 0 else None
            key = idempotency_key or f"{obligation_id}:{event_type}:{stamp}"
            if event_type == "accepted":
                key = (
                    idempotency_key
                    or f"{obligation_id}:accepted:{recommendation_key or current.date().isoformat()}"
                )
            elif event_type == "completed":
                key = f"{obligation_id}:completed"
            elif event_type == "stopped":
                key = f"{obligation_id}:stopped:{started_raw}"
            inserted = self.store.insert_action_event(
                event_id="ae:" + stable_id(key),
                obligation_id=obligation_id,
                event_type=event_type,
                occurred_at=stamp,
                payload_json=details,
                recommendation_key=recommendation_key,
                idempotency_key=key,
                connection=conn,
            )
            if not inserted:
                return {"obligation_id": obligation_id, "inserted": False}
            if event_type in {"started", "stopped", "completed"}:
                status = {
                    "started": "in_progress",
                    "stopped": "open",
                    "completed": "done",
                }[event_type]
                override = _as_float(row.get("remaining_override_hours"))
                estimated = _as_float(row.get("estimated_hours"))
                if estimated is None:
                    from .workload import (
                        DEFAULT_EFFORT_HOURS,
                        EFFORT_HOURS_BY_TAG,
                    )

                    estimated = EFFORT_HOURS_BY_TAG.get(
                        row["kind"], DEFAULT_EFFORT_HOURS
                    )
                remaining = max(
                    (
                        (override - (spent - (_as_float(row.get("spent_hours")) or 0)))
                        if override is not None
                        else estimated - spent
                    ),
                    0,
                )
                if event_type == "completed":
                    remaining = 0
                progress = (
                    100 if event_type == "completed" else row.get("progress_percent")
                )
                self.store.write_obligation_ingest(
                    obligation_id=obligation_id,
                    title=row["title"],
                    course=row.get("course"),
                    kind=row["kind"],
                    due_at=row.get("due_at"),
                    status=status,
                    source_ids=_parse_source_ids(row.get("source_ids")),
                    primary_task_id=row.get("primary_task_id"),
                    estimated_hours=row.get("estimated_hours"),
                    spent_hours=spent,
                    remaining_hours=remaining,
                    risk_band="unscored",
                    progress_percent=progress,
                    remaining_override_hours=(
                        remaining
                        if override is not None or event_type == "completed"
                        else None
                    ),
                    course_id=row.get("course_id"),
                    next_step=row.get("next_step"),
                    sources=[],
                    change_kind=(
                        "completed" if event_type == "completed" else "progress_changed"
                    ),
                    change_detail=status,
                    state_before=_state_payload(
                        obligation_id=obligation_id,
                        title=row["title"],
                        due_at=row.get("due_at"),
                        status=row["status"],
                        remaining_hours=row.get("remaining_hours"),
                    ),
                    state_after=_state_payload(
                        obligation_id=obligation_id,
                        title=row["title"],
                        due_at=row.get("due_at"),
                        status=status,
                        remaining_hours=remaining,
                    ),
                    now_iso=stamp,
                    ingest_source="action",
                    connection=conn,
                )
                if event_type == "started":
                    self.store.set_meta(_RUNNING_META, obligation_id, connection=conn)
                    self.store.set_meta(_RUNNING_STARTED_META, stamp, connection=conn)
                elif running == obligation_id:
                    self.store.set_meta(_RUNNING_META, "", connection=conn)
                    self.store.set_meta(_RUNNING_STARTED_META, "", connection=conn)
                if event_type == "completed" and spent > 0:
                    sample_key = f"{obligation_id}:effort"
                    self.store.insert_effort_sample(
                        sample_id="es:" + stable_id(sample_key),
                        obligation_id=obligation_id,
                        minutes=spent * 60,
                        completed_at=stamp,
                        source=(
                            "manual"
                            if payload and payload.get("effort_minutes") is not None
                            else "timer"
                        ),
                        course_id=row.get("course_id"),
                        kind=row["kind"],
                        idempotency_key=sample_key,
                        connection=conn,
                    )
            else:
                self.store._bump_revision(conn)
        # Compatibility JSON is a projection; a failed mirror cannot undo a committed action.
        try:
            self._mirror(obligation_id, event_type, current, details)
        except OSError:
            import logging

            logging.getLogger(__name__).warning(
                "Action saved; legacy snapshot mirror unavailable"
            )
        return {
            "obligation_id": obligation_id,
            "event_type": event_type,
            "occurred_at": stamp,
            "inserted": True,
            "recommendation_key": recommendation_key,
            "payload": details,
        }

    def _mirror(self, obligation_id, event_type, now, details):
        if event_type not in {"started", "stopped", "completed"}:
            return
        row = self.store.get_obligation(obligation_id)
        task_id = row.get("primary_task_id")
        if not task_id:
            return
        completions = load_completions(self.data_dir)
        entry = dict(completions.get(task_id) or {})
        if event_type == "started":
            entry["started_at"] = now.isoformat()
        elif event_type == "stopped":
            entry.pop("started_at", None)
        else:
            entry.update(
                done_at=now.isoformat(),
                effort_minutes=details.get("effort_minutes"),
                effort_key=row["kind"],
                course=row.get("course"),
                due_at=row.get("due_at"),
            )
        completions[task_id] = entry
        save_completions(self.data_dir, completions)
        if event_type == "completed":
            path = self.data_dir / "tasks.json"
            if path.is_file():
                tasks = load_tasks(path)
                for task in tasks:
                    if task.id in _parse_source_ids(row.get("source_ids")):
                        task.status, task.done_at = "done", now
                save_tasks(tasks, path)
