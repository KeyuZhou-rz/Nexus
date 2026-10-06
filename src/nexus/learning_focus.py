"""Course-aware learning focus assembled from tasks and trusted memory state."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.models import Task


_URGENCY_RANK = {"critical": 0, "decaying": 1, "stable": 2, "mastered": 3}


def build_learning_focus(
    tasks: list[Task],
    learning: dict[str, Any],
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Rank weak concepts and attach the nearest open task from the same course."""
    if not learning.get("available") or limit <= 0:
        return []

    candidates: dict[str, dict[str, Any]] = {}
    for evidence in learning.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        concept_id = str(evidence.get("concept_id") or "").strip()
        name = str(evidence.get("concept_name") or concept_id).strip()
        if not name:
            continue
        key = concept_id or name.casefold()
        candidate = candidates.setdefault(key, {"concept_id": concept_id, "name": name})
        candidate["course_id"] = evidence.get("course_id") or candidate.get("course_id")
        candidate["mastery"] = _number(evidence.get("current_mastery"), candidate.get("mastery"))
        candidate["urgency"] = str(evidence.get("urgency") or candidate.get("urgency") or "critical")
        trust_status = str(evidence.get("trust_status") or "unclassified")
        if not candidate.get("evidence_source") or (
            candidate.get("trust_status") != "trusted" and trust_status == "trusted"
        ):
            candidate["evidence_source"] = evidence.get("source")
            candidate["trust_status"] = trust_status
            candidate["evidence_at"] = evidence.get("timestamp")

    for review in learning.get("review_queue") or []:
        if not isinstance(review, dict):
            continue
        concept_id = str(review.get("concept_id") or "").strip()
        name = str(review.get("name") or concept_id).strip()
        if not name:
            continue
        key = concept_id or name.casefold()
        candidate = candidates.setdefault(key, {"concept_id": concept_id, "name": name})
        candidate.setdefault("mastery", _number(review.get("mastery"), None))
        candidate.setdefault("urgency", str(review.get("urgency") or "critical"))

    for weak_point in learning.get("weak_points") or []:
        if isinstance(weak_point, dict):
            name = str(weak_point.get("concept") or weak_point.get("name") or "").strip()
            course_id = weak_point.get("course") or weak_point.get("course_id")
        else:
            name = str(weak_point).strip()
            course_id = None
        if not name:
            continue
        normalized_name = name.casefold()
        key = next(
            (
                existing_key
                for existing_key, existing in candidates.items()
                if str(existing.get("name") or "").strip().casefold() == normalized_name
            ),
            normalized_name,
        )
        candidate = candidates.setdefault(key, {"concept_id": "", "name": name})
        candidate.setdefault("course_id", course_id)
        candidate.setdefault("urgency", "self_reported")
        candidate.setdefault("evidence_source", "user_profile")
        candidate.setdefault("trust_status", "user_reported")

    open_tasks = [task for task in tasks if task.status != "done" and task.due_at]
    for candidate in candidates.values():
        related = _nearest_course_task(open_tasks, candidate.get("course_id"))
        candidate["related_task"] = (
            {
                "id": related.id,
                "title": related.title,
                "due_at": related.due_at.isoformat() if related.due_at else None,
                "source": related.source,
            }
            if related
            else None
        )

    ranked = sorted(candidates.values(), key=_focus_sort_key)
    return ranked[:limit]


def _nearest_course_task(tasks: list[Task], course_id: Any) -> Task | None:
    course = str(course_id or "").strip().casefold()
    if not course:
        return None
    matches = [task for task in tasks if str(task.course or "").strip().casefold() == course]
    if not matches:
        return None
    return min(matches, key=lambda task: _timestamp(task.due_at))


def _focus_sort_key(item: dict[str, Any]) -> tuple[int, float, int, str]:
    urgency = str(item.get("urgency") or "critical")
    mastery = item.get("mastery")
    mastery_value = float(mastery) if isinstance(mastery, (int, float)) else 1.0
    has_task = 0 if item.get("related_task") else 1
    return (_URGENCY_RANK.get(urgency, 1), mastery_value, has_task, str(item.get("name", "")).casefold())


def _timestamp(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _number(value: Any, fallback: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else fallback


def knowledge_learning(
    data_dir: Path, course_id: str | None, tasks: list[Task]
) -> dict[str, Any]:
    """``build_context`` learning provider backed by the knowledge store."""
    learning = _learning_context(Path(data_dir) / "nexus.db", course_id)
    if learning.get("available"):
        learning["focus"] = build_learning_focus(tasks, learning)
    return learning


def _learning_context(db_path: Path, course_id: str | None) -> dict[str, Any]:
    if not db_path.is_file():
        return {"available": False, "reason": "knowledge_store_not_initialized"}

    try:
        from .knowledge.memory_api import MemoryAPI
        from .knowledge.progress import ProgressService
        from .knowledge.sqlite_store import SQLiteStore

        store = SQLiteStore(db_path)
        progress = ProgressService(store).get_progress(course_id)
        memory = MemoryAPI(store)
        profile = memory.get_profile()
        evidence = memory.get_evidence_summary(course_id, concept_limit=10)
    except Exception as exc:
        return {
            "available": False,
            "reason": "knowledge_store_unavailable",
            "error_type": type(exc).__name__,
        }

    return {
        "available": True,
        "course_id": progress.course_id,
        "counts": {
            "total_concepts": progress.total_concepts,
            "mastered": progress.mastered,
            "stable": progress.stable,
            "decaying": progress.decaying,
            "critical": progress.critical,
        },
        "review_queue": progress.review_queue[:20],
        "recent_trusted_events": progress.recent_events,
        "weak_points": (profile.get("weak_points") or [])[:10],
        "learning_style": profile.get("learning_style") or "default",
        "evidence": evidence,
    }
