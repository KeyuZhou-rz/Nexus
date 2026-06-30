from __future__ import annotations

from typing import Any

from nexus.knowledge.concept_resolver import ConceptResolver
from nexus.knowledge.temporal_memory import TemporalMemory
from nexus.knowledge.user_profile import UserProfileManager


def record_weak_point(
    resolver: ConceptResolver,
    profile_mgr: UserProfileManager,
    temporal: TemporalMemory,
    concept_name: str,
    course_id: str,
    *,
    session_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict:
    resolution = resolver.resolve(concept_name, course_id)
    if resolution is not None:
        profile_mgr.add_weak_point(resolution.name, course_id)
        temporal.record_event(resolution.concept_id, "struggled", session_id, context)
        return {"resolved": True, "concept_id": resolution.concept_id, "level": resolution.level}
    profile_mgr.add_weak_point(concept_name, course_id)
    return {"resolved": False, "concept_id": None, "level": None}


def apply_feedback_unified(
    resolver: ConceptResolver,
    profile_mgr: UserProfileManager,
    temporal: TemporalMemory,
    concept_name: str,
    course_id: str,
    action: str,
    *,
    session_id: str | None = None,
) -> dict:
    resolution = resolver.resolve(concept_name, course_id)
    resolved = resolution is not None
    resolved_name = resolution.name if resolution else concept_name
    concept_id = resolution.concept_id if resolution else None

    if action == "accept":
        profile_mgr.add_mastered(resolved_name, course_id)
        if resolved:
            temporal.record_event(concept_id, "correct_answer", session_id)
    elif action == "reject":
        profile_mgr.remove_weak_point(resolved_name)
        if resolved:
            temporal.record_event(concept_id, "forgot", session_id)

    return {"action": action, "resolved": resolved, "concept_id": concept_id}
