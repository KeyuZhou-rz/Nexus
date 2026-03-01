from __future__ import annotations

from datetime import datetime

from .memory_extractor import WeakPointCandidate
from .state_store import LearnerState


def apply_confidence_decay(state: LearnerState, now_iso: str, daily_decay: float = 0.98) -> LearnerState:
    try:
        now = datetime.fromisoformat(now_iso)
        then = datetime.fromisoformat(state.updated_at)
        days = max(0.0, (now - then).total_seconds() / 86400.0)
    except Exception:
        days = 0.0

    if days <= 0:
        return state

    factor = daily_decay ** days
    for topic, score in list(state.weak_point_confidence.items()):
        if not isinstance(score, (int, float)):
            continue
        state.weak_point_confidence[topic] = max(0.0, min(1.0, float(score) * factor))
    state.updated_at = now_iso
    return state


def merge_candidates_into_state(
    state: LearnerState,
    candidates: list[WeakPointCandidate],
    now_iso: str,
    threshold: float = 0.45,
) -> LearnerState:
    state.updated_at = now_iso
    for candidate in candidates:
        topic = candidate.topic.strip().lower()
        if not topic:
            continue

        old = float(state.weak_point_confidence.get(topic, 0.0))
        merged = (
            max(0.0, min(1.0, candidate.confidence))
            if old <= 0.0
            else max(0.0, min(1.0, old * 0.7 + candidate.confidence * 0.3))
        )
        state.weak_point_confidence[topic] = merged
        state.weak_point_evidence.setdefault(topic, [])
        if candidate.evidence:
            if candidate.evidence not in state.weak_point_evidence[topic]:
                state.weak_point_evidence[topic] = [
                    *state.weak_point_evidence[topic][-4:],
                    candidate.evidence,
                ]

        status = state.weak_point_status.get(topic, "active")
        if status == "rejected":
            # keep rejected unless strong renewed evidence
            if merged >= 0.85:
                state.weak_point_status[topic] = "active"
        else:
            state.weak_point_status[topic] = "active" if merged >= threshold else "accepted"

    _rebuild_views(state, threshold=threshold)
    return state


def apply_feedback(state: LearnerState, topic: str, action: str, now_iso: str) -> LearnerState:
    normalized = topic.strip().lower()
    if not normalized:
        return state

    if action == "accept":
        state.weak_point_status[normalized] = "accepted"
        state.weak_point_confidence[normalized] = min(0.4, float(state.weak_point_confidence.get(normalized, 0.4)))
    elif action == "reject":
        state.weak_point_status[normalized] = "rejected"
        state.weak_point_confidence[normalized] = 0.0
    else:
        raise ValueError("action must be 'accept' or 'reject'")

    state.corrections.append(
        {
            "topic": normalized,
            "action": action,
            "timestamp": now_iso,
        }
    )
    state.updated_at = now_iso
    _rebuild_views(state, threshold=0.45)
    return state


def _rebuild_views(state: LearnerState, threshold: float) -> None:
    active_topics = [
        topic
        for topic, status in state.weak_point_status.items()
        if status == "active" and float(state.weak_point_confidence.get(topic, 0.0)) >= threshold
    ]
    active_topics.sort(
        key=lambda topic: float(state.weak_point_confidence.get(topic, 0.0)),
        reverse=True,
    )

    state.weak_points = active_topics[:20]
    state.review_queue = active_topics[:20]
    state.mastery = {
        topic: max(0.0, min(1.0, 1.0 - float(score)))
        for topic, score in state.weak_point_confidence.items()
    }
