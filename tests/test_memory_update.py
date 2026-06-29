from __future__ import annotations

import copy
from datetime import datetime, timedelta

import pytest

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


# ---- T3: additional test cases ----

NOW_ISO = "2026-06-30T12:00:00+08:00"


def _fresh_state(**overrides) -> LearnerState:
    defaults = dict(updated_at=NOW_ISO)
    defaults.update(overrides)
    return LearnerState(**defaults)


def test_merge_sequential_evidence_accumulation():
    """Three candidates for same topic [0.5, 0.7, 0.9] -> sequential weighted merge."""
    state = _fresh_state()
    cands = [
        WeakPointCandidate(topic="paging", confidence=0.5, evidence="ev1", evidence_msg_ids=["m1"]),
        WeakPointCandidate(topic="paging", confidence=0.7, evidence="ev2", evidence_msg_ids=["m2"]),
        WeakPointCandidate(topic="paging", confidence=0.9, evidence="ev3", evidence_msg_ids=["m3"]),
    ]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    # Step 1: old=0 -> merged=0.5
    # Step 2: old=0.5 -> merged=0.5*0.7+0.7*0.3=0.56
    # Step 3: old=0.56 -> merged=0.56*0.7+0.9*0.3=0.662
    assert abs(state.weak_point_confidence["paging"] - 0.662) < 1e-6
    # All three distinct evidences should be present
    assert state.weak_point_evidence["paging"] == ["ev1", "ev2", "ev3"]


def test_merge_evidence_dedup_same_string():
    """Duplicate evidence strings for the same topic -> only kept once."""
    state = _fresh_state()
    cands = [
        WeakPointCandidate(topic="paging", confidence=0.6, evidence="same evidence", evidence_msg_ids=["m1"]),
        WeakPointCandidate(topic="paging", confidence=0.7, evidence="same evidence", evidence_msg_ids=["m2"]),
    ]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    assert state.weak_point_evidence["paging"] == ["same evidence"]


def test_merge_evidence_truncates_to_five():
    """Evidence list keeps only the last 4 + new entry (max 5) after many merges."""
    state = _fresh_state()
    evidences = [f"ev{i}" for i in range(1, 7)]  # ev1..ev6
    cands = [
        WeakPointCandidate(topic="paging", confidence=0.6, evidence=e, evidence_msg_ids=[f"m{i}"])
        for i, e in enumerate(evidences, 1)
    ]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    # After 6 distinct evidences: [-4:] of [ev1..ev5] = [ev2..ev5], + ev6
    assert state.weak_point_evidence["paging"] == ["ev2", "ev3", "ev4", "ev5", "ev6"]
    assert len(state.weak_point_evidence["paging"]) == 5


def test_merge_clamp_upper_bound():
    """Candidate confidence > 1.0 gets clamped to 1.0 on first write."""
    state = _fresh_state()
    cands = [
        WeakPointCandidate(topic="overflow", confidence=1.5, evidence="ev", evidence_msg_ids=["m1"]),
    ]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    assert state.weak_point_confidence["overflow"] == 1.0


def test_feedback_reject_cleans_review_queue():
    """Rejecting a topic removes it from both weak_points and review_queue; other topic unaffected."""
    state = _fresh_state(
        weak_points=["a", "b"],
        review_queue=["a", "b"],
        weak_point_confidence={"a": 0.8, "b": 0.8},
        weak_point_status={"a": "active", "b": "active"},
    )
    apply_feedback(state, "a", "reject", NOW_ISO)
    assert "a" not in state.weak_points
    assert "a" not in state.review_queue
    assert "b" in state.weak_points
    assert "b" in state.review_queue
    assert state.weak_point_status["a"] == "rejected"
    assert state.weak_point_confidence["a"] == 0.0


def test_feedback_accept_sets_accepted_and_caps_confidence():
    """Accept action sets status=accepted, confidence=min(0.4, current), removes from views."""
    state = _fresh_state(
        weak_points=["a"],
        review_queue=["a"],
        weak_point_confidence={"a": 0.8},
        weak_point_status={"a": "active"},
    )
    apply_feedback(state, "a", "accept", NOW_ISO)
    assert state.weak_point_status["a"] == "accepted"
    assert state.weak_point_confidence["a"] == 0.4  # min(0.4, 0.8)
    assert "a" not in state.weak_points
    assert "a" not in state.review_queue
    assert state.corrections[-1] == {"topic": "a", "action": "accept", "timestamp": NOW_ISO}


def test_feedback_invalid_action_raises_valueerror():
    """Invalid action string raises ValueError."""
    state = _fresh_state()
    with pytest.raises(ValueError, match="action must be"):
        apply_feedback(state, "a", "maybe", NOW_ISO)


def test_decay_clamp_lower_bound():
    """Very small score with aggressive decay stays >= 0.0."""
    state = _fresh_state(updated_at="2025-07-01T00:00:00+08:00", weak_point_confidence={"t": 0.001})
    apply_confidence_decay(state, now_iso=NOW_ISO, daily_decay=0.5)
    # 365 days * 0.5^365 is astronomically small but must be >= 0
    assert state.weak_point_confidence["t"] >= 0.0
    assert state.weak_point_confidence["t"] < 0.001


def test_decay_cross_day_boundary_slight():
    """2 hours elapsed (~0.083 days) with decay=0.98 -> slight decrease, updated_at refreshed."""
    then = "2026-06-29T22:00:00+08:00"
    now = "2026-06-30T00:00:00+08:00"
    state = _fresh_state(updated_at=then, weak_point_confidence={"t": 1.0})
    apply_confidence_decay(state, now_iso=now, daily_decay=0.98)
    result = state.weak_point_confidence["t"]
    assert 0.99 < result < 1.0
    assert state.updated_at == now


def test_decay_zero_or_negative_days_no_change():
    """When now == updated_at or now < updated_at, state is completely unchanged."""
    # Case 1: now == updated_at
    state1 = _fresh_state(updated_at=NOW_ISO, weak_point_confidence={"t": 0.7})
    snap1 = copy.deepcopy(state1.weak_point_confidence)
    apply_confidence_decay(state1, now_iso=NOW_ISO, daily_decay=0.5)
    assert state1.weak_point_confidence == snap1
    assert state1.updated_at == NOW_ISO  # unchanged

    # Case 2: clock skew, now before updated_at
    state2 = _fresh_state(updated_at=NOW_ISO, weak_point_confidence={"t": 0.7})
    snap2_ua = state2.updated_at
    apply_confidence_decay(state2, now_iso="2026-06-29T12:00:00+08:00", daily_decay=0.5)
    assert state2.weak_point_confidence["t"] == 0.7
    assert state2.updated_at == snap2_ua


@pytest.mark.xfail(
    reason="BUG: merge_candidates_into_state treats old<=0.0 as first-write even for rejected topics, "
           "bypassing the 0.7/0.3 decay weighting. A rejected topic with confidence=0.0 gets full "
           "candidate confidence instead of the expected old*0.7+cand*0.3, causing false revival.",
    strict=True,
)
def test_rejected_revival_no_revive_when_low_merged():
    """Rejected topic with confidence=0.0 receiving candidate confidence=0.9 should compute
    merged=0.0*0.7+0.9*0.3=0.27 and stay rejected, but code uses first-write path instead."""
    state = _fresh_state(
        weak_point_status={"a": "rejected"},
        weak_point_confidence={"a": 0.0},
    )
    cands = [WeakPointCandidate(topic="a", confidence=0.9, evidence="ev1", evidence_msg_ids=[])]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    # Expected: merged = 0.0*0.7 + 0.9*0.3 = 0.27 < 0.85 -> stays rejected
    assert state.weak_point_status["a"] == "rejected"
    assert abs(state.weak_point_confidence["a"] - 0.27) < 1e-6


def test_rejected_revival_triggers_at_high_merged():
    """Rejected topic with existing high confidence + high candidate -> merged >= 0.85 -> revival."""
    state = _fresh_state(
        weak_point_status={"a": "rejected"},
        weak_point_confidence={"a": 0.95},
        weak_point_evidence={"a": []},
    )
    cands = [WeakPointCandidate(topic="a", confidence=0.95, evidence="ev2", evidence_msg_ids=[])]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    # merged = clamp(0.95*0.7 + 0.95*0.3) = 0.95 >= 0.85 -> revives to active
    assert state.weak_point_status["a"] == "active"
    assert "a" in state.weak_points


def test_merge_empty_candidates_updates_timestamp_only():
    """Merging empty candidate list still updates updated_at; _rebuild_views rebuilds views
    from existing state (x: active, conf 0.5 >= 0.45) so it surfaces in weak_points/review_queue/mastery."""
    state_before = _fresh_state(weak_point_confidence={"x": 0.5}, weak_point_status={"x": "active"})
    snap = copy.deepcopy(state_before.weak_point_confidence)
    merge_candidates_into_state(state_before, [], now_iso="2026-07-01T00:00:00+08:00")
    assert state_before.weak_point_confidence == snap
    assert state_before.updated_at == "2026-07-01T00:00:00+08:00"
    # _rebuild_views side effect: existing active topic above threshold surfaces in views
    assert state_before.weak_points == ["x"]
    assert state_before.review_queue == ["x"]
    assert state_before.mastery == {"x": 0.5}


def test_merge_blank_topic_skipped():
    """Candidate with blank/whitespace-only topic is silently skipped."""
    state = _fresh_state()
    cands = [
        WeakPointCandidate(topic="   ", confidence=0.8, evidence="ev", evidence_msg_ids=["m1"]),
    ]
    merge_candidates_into_state(state, cands, now_iso=NOW_ISO)
    assert state.weak_point_confidence == {}
    assert state.weak_point_status == {}
