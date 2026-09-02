"""Canonical academic state — SQLite is the source of truth."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .academic_state import (
    _as_float,
    _effort_snapshot,
    _parse_source_ids,
    _source_rank,
    display_course,
    normalize_course,
    obligation_id_for_task,
)
from .intelligence.workload import effort_key
from .knowledge.sqlite_store import SQLiteStore
from .models import Task
from .task_service import load_completions
from .utils import stable_id
from .v1_config import INACTIVE_OBLIGATION_STATUSES


def _resolve_now(now: datetime | None) -> datetime:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        return current.astimezone()
    return current


def _merge_status(prior: dict[str, Any] | None, members: list[Task]) -> str:
    if any(getattr(item, "status", "open") == "done" for item in members):
        return "done"
    if prior is not None:
        previous = str(prior.get("status") or "open")
        if previous in INACTIVE_OBLIGATION_STATUSES or previous == "in_progress":
            return previous
    return "open"


def _due_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _state_payload(
    *,
    obligation_id: str,
    title: str,
    due_at: str | None,
    status: str,
    progress_percent: Any = None,
    remaining_hours: Any = None,
) -> dict[str, Any]:
    return {
        "id": obligation_id,
        "title": title,
        "due_at": due_at,
        "status": status,
        "progress_percent": progress_percent,
        "remaining_hours": remaining_hours,
    }


class AcademicStateService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir).expanduser()
        self._store = SQLiteStore(self._data_dir / "nexus.db")

    @property
    def store(self) -> SQLiteStore:
        return self._store

    def ingest_snapshot(
        self,
        tasks: Iterable[Task],
        *,
        now: datetime | None = None,
        reconcile_missing: bool = False,
        source: str = "ingest",
    ) -> dict[str, int]:
        grouped: dict[str, list[Task]] = {}
        skipped = 0
        for task in tasks:
            key = obligation_id_for_task(task)
            if key is None:
                skipped += 1
                continue
            grouped.setdefault(key, []).append(task)

        completions = load_completions(self._data_dir)
        now_local = _resolve_now(now)
        now_iso = now_local.isoformat()
        store = self._store
        existing = {row["id"]: row for row in store.list_obligations()}
        written = 0
        created = 0
        due_changed = 0
        for key, members in grouped.items():
            members.sort(key=_source_rank)
            primary = members[0]
            prior = existing.get(key)
            status = _merge_status(prior, members)
            due_at = next((item.due_at for item in members if item.due_at is not None), None)
            due_iso = due_at.isoformat() if due_at else None
            source_ids = [item.id for item in members]
            estimated, spent, remaining, risk_band = _effort_snapshot(
                primary, source_ids, completions, due_at, status, now_local
            )
            persist_estimated = None
            if primary.estimated_hours is not None:
                persist_estimated = float(primary.estimated_hours)
                estimated = persist_estimated
                if status not in INACTIVE_OBLIGATION_STATUSES:
                    remaining = max(estimated - spent, 0.0)
            elif prior is not None and prior.get("estimated_hours") is not None:
                persist_estimated = _as_float(prior.get("estimated_hours"))
            if prior is not None:
                spent = max(spent, _as_float(prior.get("spent_hours")) or 0.0)
            remaining_override = None
            if primary.remaining_hours is not None:
                remaining_override = float(primary.remaining_hours)
            elif prior is not None and prior.get("remaining_override_hours") is not None:
                remaining_override = float(prior["remaining_override_hours"])
            if status in INACTIVE_OBLIGATION_STATUSES:
                remaining = 0.0
            elif remaining_override is not None:
                remaining = remaining_override
            progress_percent = prior.get("progress_percent") if prior else None
            next_step = prior.get("next_step") if prior else None
            course_id = self._upsert_course_from_name(primary.course, now_iso=now_iso)
            if course_id is None and prior is not None:
                course_id = prior.get("course_id")
            change_kind = None
            change_detail = None
            state_before = None
            if prior is None:
                change_kind = "created"
                change_detail = (primary.title or "").strip()
                created += 1
            elif (prior.get("due_at") or None) != due_iso:
                change_kind = "due_changed"
                change_detail = f"{prior.get('due_at') or '—'} → {due_iso or '—'}"
                state_before = _state_payload(
                    obligation_id=key,
                    title=str(prior.get("title") or primary.title or ""),
                    due_at=prior.get("due_at") or None,
                    status=str(prior.get("status") or "open"),
                    progress_percent=prior.get("progress_percent"),
                    remaining_hours=prior.get("remaining_hours"),
                )
                due_changed += 1
            title = (primary.title or "").strip()
            state_after = _state_payload(
                obligation_id=key,
                title=title,
                due_at=due_iso,
                status=status,
                progress_percent=progress_percent,
                remaining_hours=remaining,
            )
            store.write_obligation_ingest(
                obligation_id=key,
                title=title,
                course=primary.course,
                kind=effort_key(primary),
                due_at=due_iso,
                status=status,
                source_ids=source_ids,
                primary_task_id=primary.id,
                estimated_hours=persist_estimated,
                spent_hours=spent,
                remaining_hours=remaining,
                risk_band=risk_band,
                progress_percent=progress_percent,
                remaining_override_hours=remaining_override,
                course_id=course_id,
                next_step=next_step,
                sources=self._sources_from_members(members, now_iso),
                change_kind=change_kind,
                change_detail=change_detail,
                state_before=state_before,
                state_after=state_after,
                now_iso=now_iso,
                ingest_source=source,
            )
            written += 1

        missing = 0
        if reconcile_missing:
            inactive = set(INACTIVE_OBLIGATION_STATUSES) | {"missing"}
            for key, prior in existing.items():
                if key in grouped:
                    continue
                if prior.get("status") in inactive:
                    continue
                title = str(prior.get("title") or key)
                state_before = _state_payload(
                    obligation_id=key,
                    title=title,
                    due_at=prior.get("due_at") or None,
                    status=str(prior.get("status") or "open"),
                    progress_percent=prior.get("progress_percent"),
                    remaining_hours=prior.get("remaining_hours"),
                )
                store.write_obligation_ingest(
                    obligation_id=key,
                    title=title,
                    course=prior.get("course"),
                    kind=str(prior.get("kind") or "assignment"),
                    due_at=prior.get("due_at"),
                    status="missing",
                    source_ids=_parse_source_ids(prior.get("source_ids")),
                    primary_task_id=prior.get("primary_task_id"),
                    estimated_hours=prior.get("estimated_hours"),
                    spent_hours=prior.get("spent_hours"),
                    remaining_hours=prior.get("remaining_hours"),
                    risk_band="unscored",
                    progress_percent=prior.get("progress_percent"),
                    remaining_override_hours=prior.get("remaining_override_hours"),
                    course_id=prior.get("course_id"),
                    next_step=prior.get("next_step"),
                    sources=[],
                    change_kind="disappeared",
                    change_detail=title,
                    state_before=state_before,
                    state_after=_state_payload(
                        obligation_id=key,
                        title=title,
                        due_at=prior.get("due_at") or None,
                        status="missing",
                        progress_percent=prior.get("progress_percent"),
                        remaining_hours=prior.get("remaining_hours"),
                    ),
                    now_iso=now_iso,
                    ingest_source=source,
                )
                missing += 1
        return {
            "upserted": written,
            "skipped": skipped,
            "created": created,
            "due_changed": due_changed,
            "missing": missing,
        }

    def load_state(self) -> dict[str, Any]:
        obligations = []
        for row in self._store.list_obligations():
            item = dict(row)
            item["source_ids"] = _parse_source_ids(item.get("source_ids"))
            obligations.append(item)
        return {
            "revision": self._store.get_state_revision(),
            "obligations": obligations,
            "courses": self._store.list_courses(),
            "calendar_blocks": self._store.list_calendar_blocks(),
            "availability_rules": self._store.list_availability_rules(),
            "availability_overrides": self._store.list_availability_overrides(),
            "state_changes": self._store.list_state_changes(limit=200),
            "action_events": self._store.list_action_events(limit=200),
        }

    def add_manual_obligation(
        self,
        *,
        title: str,
        course: str | None = None,
        due_at: datetime | str | None = None,
        estimated_hours: float | None = None,
        remaining_hours: float | None = None,
        kind: str | None = None,
        next_step: str | None = None,
        tags: list[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = (title or "").strip()
        if not normalized:
            raise ValueError("title is required")
        current = _resolve_now(now)
        parsed_due = due_at
        if isinstance(due_at, str):
            parsed_due = datetime.fromisoformat(due_at)
        tag_list = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if kind and kind not in tag_list:
            tag_list.append(kind)
        if "manual" not in {tag.lower() for tag in tag_list}:
            tag_list.insert(0, "manual")
        course_name = course.strip() if course and str(course).strip() else None
        task = Task(
            id=f"manual:{uuid4().hex}",
            title=normalized,
            due_at=parsed_due,
            source="manual",
            course=course_name,
            tags=tag_list,
            estimated_hours=estimated_hours,
            remaining_hours=remaining_hours,
            received_at=current,
        )
        self.ingest_snapshot([task], now=current, source="manual")
        oid = obligation_id_for_task(task)
        if oid is None:
            raise ValueError("manual item is not an obligation candidate")
        if next_step is not None:
            return self.update_obligation(oid, next_step=next_step, now=current)
        row = self._store.get_obligation(oid)
        if row is None:
            raise KeyError(oid)
        return row

    def update_obligation(
        self,
        obligation_id: str,
        *,
        due_at: Any = ...,
        progress_percent: Any = ...,
        remaining_hours: Any = ...,
        next_step: Any = ...,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        row = self._store.get_obligation(obligation_id)
        if row is None:
            raise KeyError(obligation_id)
        current = _resolve_now(now)
        now_iso = current.isoformat()
        old_due = row.get("due_at") or None
        new_due = old_due if due_at is ... else _due_iso(due_at)
        new_progress = row.get("progress_percent")
        if progress_percent is not ...:
            new_progress = None if progress_percent is None else int(progress_percent)
        new_next = row.get("next_step") if next_step is ... else next_step
        override = row.get("remaining_override_hours")
        if remaining_hours is not ...:
            override = None if remaining_hours is None else float(remaining_hours)
        estimated = _as_float(row.get("estimated_hours")) or 0.0
        spent = _as_float(row.get("spent_hours")) or 0.0
        if override is not None:
            remaining = float(override)
        else:
            remaining = max(estimated - spent, 0.0)
        change_kind = None
        change_detail = None
        if due_at is not ... and new_due != old_due:
            change_kind = "due_changed"
            change_detail = f"{old_due or '—'} → {new_due or '—'}"
        elif (
            progress_percent is not ...
            or remaining_hours is not ...
            or next_step is not ...
        ):
            change_kind = "progress_changed"
        title = str(row.get("title") or "")
        status = str(row.get("status") or "open")
        self._store.write_obligation_ingest(
            obligation_id=obligation_id,
            title=title,
            course=row.get("course"),
            kind=str(row.get("kind") or "assignment"),
            due_at=new_due,
            status=status,
            source_ids=_parse_source_ids(row.get("source_ids")),
            primary_task_id=row.get("primary_task_id"),
            estimated_hours=row.get("estimated_hours"),
            spent_hours=row.get("spent_hours"),
            remaining_hours=remaining,
            risk_band=row.get("risk_band"),
            progress_percent=new_progress,
            remaining_override_hours=override,
            course_id=row.get("course_id"),
            next_step=new_next,
            sources=[],
            change_kind=change_kind,
            change_detail=change_detail,
            state_before=_state_payload(
                obligation_id=obligation_id,
                title=title,
                due_at=old_due,
                status=status,
                progress_percent=row.get("progress_percent"),
                remaining_hours=row.get("remaining_hours"),
            ),
            state_after=_state_payload(
                obligation_id=obligation_id,
                title=title,
                due_at=new_due,
                status=status,
                progress_percent=new_progress,
                remaining_hours=remaining,
            ),
            now_iso=now_iso,
            ingest_source="manual",
        )
        updated = self._store.get_obligation(obligation_id)
        if updated is None:
            raise KeyError(obligation_id)
        return updated

    def upsert_calendar_block(
        self,
        *,
        source: str,
        external_id: str,
        title: str,
        starts_at: datetime | str,
        ends_at: datetime | str,
        kind: str = "busy",
        course_id: str | None = None,
        status: str = "confirmed",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = _resolve_now(now)
        now_iso = current.isoformat()
        starts_iso = _due_iso(starts_at) or ""
        ends_iso = _due_iso(ends_at) or ""
        prior = next(
            (
                block
                for block in self._store.list_calendar_blocks()
                if block["source"] == source and block["external_id"] == external_id
            ),
            None,
        )
        row = self._store.upsert_calendar_block(
            source=source,
            external_id=external_id,
            title=title,
            starts_at=starts_iso,
            ends_at=ends_iso,
            kind=kind,
            course_id=course_id,
            status=status,
            observed_at=now_iso,
        )
        interval_changed = (
            prior is None
            or prior.get("starts_at") != starts_iso
            or prior.get("ends_at") != ends_iso
        )
        if interval_changed:
            self._store.record_state_change(
                entity_type="calendar_block",
                entity_id=str(row["id"]),
                kind="calendar_changed",
                before_json=None
                if prior is None
                else {
                    "starts_at": prior.get("starts_at"),
                    "ends_at": prior.get("ends_at"),
                },
                after_json={
                    "starts_at": starts_iso,
                    "ends_at": ends_iso,
                    "title": title,
                },
                source=source,
                created_at=now_iso,
            )
        self._store.bump_state_revision()
        return row

    def add_availability_rule(
        self,
        *,
        weekday: int,
        starts_local: str,
        ends_local: str,
        timezone: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        row = self._store.upsert_availability_rule(
            weekday=weekday,
            starts_local=starts_local,
            ends_local=ends_local,
            timezone=timezone,
            enabled=enabled,
        )
        self._store.bump_state_revision()
        return row

    def add_availability_override(
        self,
        *,
        starts_at: datetime | str,
        ends_at: datetime | str,
        kind: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        row = self._store.upsert_availability_override(
            starts_at=_due_iso(starts_at),
            ends_at=_due_iso(ends_at),
            kind=kind,
            note=note,
        )
        self._store.bump_state_revision()
        return row

    def remove_availability_rule(self, rule_id: str) -> None:
        self._store.delete_availability_rule(rule_id)
        self._store.bump_state_revision()

    def remove_availability_override(self, override_id: str) -> None:
        self._store.delete_availability_override(override_id)
        self._store.bump_state_revision()

    def import_legacy_completions(self, *, now: datetime | None = None) -> dict[str, int]:
        current = _resolve_now(now)
        now_iso = current.isoformat()
        completions = load_completions(self._data_dir)
        events = 0
        samples = 0
        skipped = 0
        index = self._external_index()
        for task_id, entry in completions.items():
            if not isinstance(entry, dict):
                skipped += 1
                continue
            row = index.get(str(task_id))
            if row is None:
                skipped += 1
                continue
            oid = str(row["id"])
            existing_types = {
                str(item.get("event_type"))
                for item in self._store.list_action_events(obligation_id=oid, limit=50)
            }
            existing_samples = self._store.list_effort_samples(obligation_id=oid)
            done_at = entry.get("done_at")
            started_at = entry.get("started_at")
            minutes = entry.get("effort_minutes")
            if done_at and "completed" in existing_types:
                skipped += 1
                continue
            if done_at:
                key = f"legacy:{task_id}:completed"
                if self._store.insert_action_event(
                    event_id=f"ae:{stable_id(key)}",
                    obligation_id=oid,
                    event_type="completed",
                    occurred_at=str(done_at),
                    payload_json={"task_id": task_id},
                    idempotency_key=key,
                ):
                    events += 1
                status = str(row.get("status") or "open")
                if status not in {"done", "completed", "cancelled"}:
                    title = str(row.get("title") or oid)
                    self._store.write_obligation_ingest(
                        obligation_id=oid,
                        title=title,
                        course=row.get("course"),
                        kind=str(row.get("kind") or "assignment"),
                        due_at=row.get("due_at"),
                        status="done",
                        source_ids=_parse_source_ids(row.get("source_ids")),
                        primary_task_id=row.get("primary_task_id"),
                        estimated_hours=row.get("estimated_hours"),
                        spent_hours=row.get("spent_hours"),
                        remaining_hours=0.0,
                        risk_band=row.get("risk_band"),
                        progress_percent=row.get("progress_percent"),
                        remaining_override_hours=row.get("remaining_override_hours"),
                        course_id=row.get("course_id"),
                        next_step=row.get("next_step"),
                        sources=[],
                        change_kind="completed",
                        change_detail=str(task_id),
                        state_before=_state_payload(
                            obligation_id=oid,
                            title=title,
                            due_at=row.get("due_at") or None,
                            status=status,
                            progress_percent=row.get("progress_percent"),
                            remaining_hours=row.get("remaining_hours"),
                        ),
                        state_after=_state_payload(
                            obligation_id=oid,
                            title=title,
                            due_at=row.get("due_at") or None,
                            status="done",
                            progress_percent=row.get("progress_percent"),
                            remaining_hours=0.0,
                        ),
                        now_iso=now_iso,
                        ingest_source="legacy_import",
                    )
                    row = self._store.get_obligation(oid) or row
                    index[str(task_id)] = row
            elif started_at:
                key = f"legacy:{task_id}:started"
                if self._store.insert_action_event(
                    event_id=f"ae:{stable_id(key)}",
                    obligation_id=oid,
                    event_type="started",
                    occurred_at=str(started_at),
                    payload_json={"task_id": task_id},
                    idempotency_key=key,
                ):
                    events += 1
            if existing_samples:
                continue
            if isinstance(minutes, (int, float)) and minutes > 0:
                key = f"legacy:{task_id}:effort"
                if self._store.insert_effort_sample(
                    sample_id=f"es:{stable_id(key)}",
                    obligation_id=oid,
                    minutes=float(minutes),
                    completed_at=str(done_at or now_iso),
                    source="legacy",
                    course_id=row.get("course_id"),
                    kind=row.get("kind"),
                    idempotency_key=key,
                ):
                    samples += 1
        if self._store.get_meta("legacy_completions_imported_at") is None:
            self._store.set_meta("legacy_completions_imported_at", now_iso)
        return {"events": events, "samples": samples, "skipped": skipped}

    def _upsert_course_from_name(
        self, raw: str | None, *, now_iso: str
    ) -> str | None:
        name = (raw or "").strip()
        if not name:
            return None
        course_id = "course:" + stable_id(normalize_course(name))
        label = display_course(name) or name
        self._store.upsert_course(course_id, label, active=True, now_iso=now_iso)
        return course_id

    def _sources_from_members(
        self, members: list[Task], now_iso: str
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for index, item in enumerate(members):
            due_iso = item.due_at.isoformat() if item.due_at else ""
            sources.append(
                {
                    "source": item.source or "unknown",
                    "external_id": item.id,
                    "observed_at": now_iso,
                    "payload_hash": stable_id(f"{item.id}|{item.title}|{due_iso}"),
                    "is_primary": index == 0,
                }
            )
        return sources

    def _external_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for row in self._store.list_obligations():
            for source_id in _parse_source_ids(row.get("source_ids")):
                index.setdefault(source_id, row)
            for src in self._store.list_obligation_sources(row["id"]):
                index.setdefault(str(src["external_id"]), row)
        return index
