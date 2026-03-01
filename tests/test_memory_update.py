from __future__ import annotations

from datetime import datetime, timedelta

from nexus.memory_extractor import WeakPointCandidate
from nexus.memory_update import apply_confidence_decay, apply_feedback, merge_candidates_into_state
from nexus.state_store import LearnerState


def test_merge_candidates_into_state_builds_active_topics():
    state = LearnerState()
    cands = [
        WeakPointCandidate(
            topic="op-amp feedback",
            confidence=0.9,
            evidence="user says confused",
            evidence_msg_ids=["m1"],
        )
    ]
    updated = merge_candidates_into_state(state, cands, now_iso=datetime.now().astimezone().isoformat())
    assert "op-amp feedback" in updated.weak_points
    assert updated.weak_point_status["op-amp feedback"] == "active"


def test_apply_feedback_reject_removes_active():
    state = LearnerState(
        weak_points=["op-amp feedback"],
        review_queue=["op-amp feedback"],
        weak_point_confidence={"op-amp feedback": 0.8},
        weak_point_status={"op-amp feedback": "active"},
    )
    updated = apply_feedback(state, "op-amp feedback", "reject", datetime.now().astimezone().isoformat())
    assert "op-amp feedback" not in updated.weak_points
    assert updated.weak_point_status["op-amp feedback"] == "rejected"


def test_apply_confidence_decay_decreases_scores():
    now = datetime.now().astimezone()
    old = (now - timedelta(days=5)).isoformat()
    state = LearnerState(updated_at=old, weak_point_confidence={"topic": 1.0})
    updated = apply_confidence_decay(state, now_iso=now.isoformat(), daily_decay=0.9)
    assert updated.weak_point_confidence["topic"] < 1.0
