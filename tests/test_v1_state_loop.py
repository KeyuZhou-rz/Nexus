"""Mandatory V1 State → Delta → Risk → Recommendation → Action → Feedback loop."""
from __future__ import annotations

from datetime import timedelta

from nexus.academic_state_service import AcademicStateService
from nexus.action_service import ActionService
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.today import build_today
from nexus.v1_config import V1_NOW

from tests.fixtures.v1_loop import (
    CS_TITLE,
    HISTORY_TITLE,
    conflict_block_leaving_two_hours,
    cs_due_moved,
    cs_due_original,
    history_due,
    twelve_hour_rules,
)


def _seed_loop(data_dir):
    state = AcademicStateService(data_dir)
    cs = state.add_manual_obligation(
        title=CS_TITLE,
        course="CS101",
        due_at=cs_due_original(),
        estimated_hours=6,
        remaining_hours=6,
        kind="assignment",
        now=V1_NOW,
    )
    history = state.add_manual_obligation(
        title=HISTORY_TITLE,
        course="HIST101",
        due_at=history_due(),
        estimated_hours=1,
        remaining_hours=1,
        kind="reading",
        now=V1_NOW,
    )
    for rule in twelve_hour_rules():
        state.add_availability_rule(**rule)
    return state, cs["id"], history["id"]


def test_deadline_jump_and_completion_rerank(tmp_path) -> None:
    state, cs_id, history_id = _seed_loop(tmp_path)
    before = build_today(tmp_path, now=V1_NOW)
    cs_risk = next(item for item in before.assessments if item.obligation_id == cs_id)
    assert cs_risk.available_hours == 12
    assert cs_risk.band != "high"

    state.update_obligation(cs_id, due_at=cs_due_moved(), now=V1_NOW)
    block = conflict_block_leaving_two_hours()
    state.upsert_calendar_block(
        source=block["source"],
        external_id=block["external_id"],
        title=block["title"],
        starts_at=block["starts_at"],
        ends_at=block["ends_at"],
        kind=block["kind"],
        now=V1_NOW,
    )

    today = build_today(tmp_path, now=V1_NOW)
    rec = today.recommendation
    assert rec is not None
    assert rec.obligation_id == cs_id
    cs_risk = next(item for item in today.assessments if item.obligation_id == cs_id)
    assert cs_risk.obligation_id == cs_id
    assert cs_risk.band == "high"
    assert "remaining_exceeds_available" in cs_risk.reasons
    assert cs_risk.available_hours == 2
    assert cs_risk.remaining_hours == 6
    due_changes = [
        item
        for item in state.store.list_state_changes(entity_id=cs_id)
        if item["kind"] == "due_changed"
    ]
    assert len(due_changes) == 1
    assert rec.title == CS_TITLE
    assert rec.block_minutes > 0
    assert CS_TITLE in rec.first_step or rec.first_step.startswith("Open")

    actions = ActionService(tmp_path)
    actions.accept(cs_id, block_minutes=rec.block_minutes, recommendation_key=rec.recommendation_key, now=V1_NOW)
    actions.start(cs_id, recommendation_key=rec.recommendation_key, now=V1_NOW)
    done_at = V1_NOW + timedelta(minutes=90)
    actions.complete(cs_id, now=done_at)

    restarted = SQLiteStore(tmp_path / "nexus.db")
    row = restarted.get_obligation(cs_id)
    assert row["id"] == cs_id
    assert row["status"] == "done"
    samples = restarted.list_effort_samples(obligation_id=cs_id)
    assert len(samples) == 1
    assert float(samples[0]["minutes"]) == 90
    events = [item["event_type"] for item in restarted.list_action_events(obligation_id=cs_id)]
    assert events.count("completed") == 1
    assert "accepted" in events and "started" in events

    after = build_today(tmp_path, now=done_at)
    assert after.recommendation is not None
    assert after.recommendation.obligation_id == history_id
    active_ids = {item.obligation_id for item in after.assessments}
    assert cs_id not in active_ids
    assert HISTORY_TITLE in (after.recommendation.title or "")


def test_unknown_availability_is_not_high(tmp_path) -> None:
    state = AcademicStateService(tmp_path)
    row = state.add_manual_obligation(
        title=CS_TITLE,
        course="CS101",
        due_at=cs_due_moved(),
        remaining_hours=6,
        kind="assignment",
        now=V1_NOW,
    )
    today = build_today(tmp_path, now=V1_NOW)
    risk = next(item for item in today.assessments if item.obligation_id == row["id"])
    assert risk.band == "unscored"
    assert risk.available_hours is None
    assert today.recommendation is not None
    assert today.recommendation.action == "setup"
    assert today.recommendation.missing_input
    assert "study" in today.recommendation.missing_input.lower() or "availability" in (
        today.recommendation.missing_input or ""
    ).lower()
