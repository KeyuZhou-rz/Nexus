"""Accept / start / stop / complete / defer against obligation IDs."""
from __future__ import annotations

from datetime import timedelta

import pytest

from nexus.core.academic_state_service import AcademicStateService
from nexus.core.action_service import ActionService
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.core.today import build_today
from nexus.core.v1_config import V1_NOW

from tests.fixtures.v1_loop import CS_TITLE, cs_due_original


def _seed(tmp_path):
    service = AcademicStateService(tmp_path)
    row = service.add_manual_obligation(
        title=CS_TITLE,
        course="CS101",
        due_at=cs_due_original(),
        estimated_hours=6,
        remaining_hours=6,
        kind="assignment",
        now=V1_NOW,
    )
    return service, ActionService(tmp_path), row["id"]


def test_accept_start_stop_complete_are_durable(tmp_path) -> None:
    _, actions, oid = _seed(tmp_path)
    actions.accept(oid, block_minutes=50, recommendation_key="rk:test", now=V1_NOW)
    actions.start(oid, recommendation_key="rk:test", now=V1_NOW)
    running = actions.running_action(now=V1_NOW + timedelta(minutes=20))
    assert running is not None
    assert running["obligation_id"] == oid
    assert running["elapsed_minutes"] == 20

    actions.stop(oid, now=V1_NOW + timedelta(minutes=20))
    assert actions.running_action(now=V1_NOW + timedelta(minutes=21)) is None
    row = SQLiteStore(tmp_path / "nexus.db").get_obligation(oid)
    assert float(row["spent_hours"]) > 0

    actions.start(oid, now=V1_NOW + timedelta(minutes=30))
    actions.complete(oid, now=V1_NOW + timedelta(minutes=30) + timedelta(minutes=90))
    store = SQLiteStore(tmp_path / "nexus.db")
    row = store.get_obligation(oid)
    assert row["status"] == "done"
    assert float(row["remaining_hours"]) == 0.0
    assert int(row["progress_percent"]) == 100
    events = {item["event_type"] for item in store.list_action_events(obligation_id=oid)}
    assert {"accepted", "started", "stopped", "completed"} <= events
    samples = store.list_effort_samples(obligation_id=oid)
    assert len(samples) == 1
    assert float(samples[0]["minutes"]) == 110
    assert float(row["spent_hours"]) == pytest.approx(110 / 60)


def test_complete_without_timer_does_not_invent_effort(tmp_path) -> None:
    _, actions, oid = _seed(tmp_path)
    actions.complete(oid, now=V1_NOW)
    store = SQLiteStore(tmp_path / "nexus.db")
    assert store.get_obligation(oid)["status"] == "done"
    assert store.list_effort_samples(obligation_id=oid) == []


def test_complete_is_idempotent(tmp_path) -> None:
    _, actions, oid = _seed(tmp_path)
    actions.complete(oid, effort_minutes=30, now=V1_NOW)
    actions.complete(oid, effort_minutes=30, now=V1_NOW + timedelta(minutes=1))
    store = SQLiteStore(tmp_path / "nexus.db")
    completed = [
        item
        for item in store.list_action_events(obligation_id=oid)
        if item["event_type"] == "completed"
    ]
    assert len(completed) == 1
    assert len(store.list_effort_samples(obligation_id=oid)) == 1


def test_defer_does_not_change_deadline_or_complete(tmp_path) -> None:
    service, actions, oid = _seed(tmp_path)
    due = service.store.get_obligation(oid)["due_at"]
    actions.defer(oid, reason="exam tomorrow", now=V1_NOW)
    row = service.store.get_obligation(oid)
    assert row["due_at"] == due
    assert row["status"] == "open"
    kinds = {item["event_type"] for item in service.store.list_action_events(obligation_id=oid)}
    assert "deferred" in kinds


def test_complete_survives_snapshot_refresh(tmp_path) -> None:
    from nexus.core.academic_state import persist_obligations
    from nexus.core.models import Task

    service, actions, oid = _seed(tmp_path)
    actions.complete(oid, effort_minutes=90, now=V1_NOW)
    persist_obligations(
        tmp_path,
        [
            Task(
                id="manual:cs-assignment-2",
                title=CS_TITLE,
                due_at=cs_due_original(),
                source="manual",
                course="CS101",
                tags=["assignment", "manual"],
            )
        ],
    )
    row = service.store.get_obligation(oid)
    assert row["status"] == "done"
    today = build_today(tmp_path, now=V1_NOW)
    assert oid not in {item.obligation_id for item in today.assessments}


def test_buttons_bind_obligation_id_not_task_id(tmp_path) -> None:
    _, actions, oid = _seed(tmp_path)
    assert oid.startswith("ob:")
    actions.start(oid, now=V1_NOW)
    assert actions.running_action(now=V1_NOW)["obligation_id"] == oid
