"""
Progress Service — Phase 4D.

Single entry point for all progress-related queries: mastery, assessment,
review queue, study plans, gap scanning, and retrospectives.

Module tiering (4D-2):
  CORE         — always active, affects mastery
  CONDITIONAL  — enabled when user opts in (e.g., has exam dates)
  EXPERIMENTAL — disabled by default, never affects core mastery
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Module tier registry (4D-2) ──────────────────────────────────────────

MODULE_TIERS: dict[str, str] = {
    "temporal_memory":  "core",
    "assessment":       "core",
    "user_profile":     "core",
    "study_planner":    "conditional",
    "sprint_planner":   "conditional",
    "gap_scanner":      "experimental",
    "retrospective":    "experimental",
}

CORE_MODULES = {m for m, t in MODULE_TIERS.items() if t == "core"}
CONDITIONAL_MODULES = {m for m, t in MODULE_TIERS.items() if t == "conditional"}
EXPERIMENTAL_MODULES = {m for m, t in MODULE_TIERS.items() if t == "experimental"}


def is_experimental(module_name: str) -> bool:
    return MODULE_TIERS.get(module_name) == "experimental"


def is_core(module_name: str) -> bool:
    return MODULE_TIERS.get(module_name) == "core"


# ── ProgressService ───────────────────────────────────────────────────────


@dataclass
class ProgressReport:
    """Aggregated progress snapshot for a course."""
    course_id: str
    total_concepts: int = 0
    mastered: int = 0
    stable: int = 0
    decaying: int = 0
    critical: int = 0
    review_queue: list[dict] = field(default_factory=list)
    recent_events: int = 0
    last_assessment_at: str | None = None


class ProgressService:
    """Unified facade for progress queries (4D-1).

    Usage:
        from nexus.knowledge.progress import ProgressService
        from nexus.knowledge.sqlite_store import SQLiteStore

        store = SQLiteStore(Path("data/nexus.db"))
        svc = ProgressService(store)
        report = svc.get_progress(course_id="CS202_OS")
        print(report.mastered, report.critical)
    """

    def __init__(self, sqlite_store) -> None:
        from .temporal_memory import TemporalMemory
        from .user_profile import UserProfileManager

        self._store = sqlite_store
        self._temporal = TemporalMemory(sqlite_store)
        self._profile = UserProfileManager(sqlite_store)

    # ── Mastery ──────────────────────────────────────────────────────

    def get_progress(self, course_id: str | None = None) -> ProgressReport:
        """Return a consolidated progress snapshot."""
        snapshots = self._temporal.get_all_mastery(course_id)
        review = self._temporal.get_decaying_concepts(course_id=course_id)

        report = ProgressReport(course_id=course_id or "all")
        report.total_concepts = len(snapshots)
        report.review_queue = [
            {"concept_id": r.concept_id, "name": r.concept_name,
             "mastery": round(r.current_mastery, 3), "urgency": r.urgency}
            for r in review
        ]

        for s in snapshots:
            urgency = s.urgency
            if urgency == "mastered":
                report.mastered += 1
            elif urgency == "stable":
                report.stable += 1
            elif urgency == "decaying":
                report.decaying += 1
            elif urgency == "critical":
                report.critical += 1

        # Count trusted recent events
        report.recent_events = self._count_trusted_events(course_id)
        return report

    def get_mastery(self, concept_id: str) -> float:
        return self._temporal.get_mastery(concept_id)

    def get_mastery_map(self, course_id: str | None = None) -> dict[str, float]:
        snapshots = self._temporal.get_all_mastery(course_id)
        return {s.concept_id: s.current_mastery for s in snapshots}

    # ── Assessment ────────────────────────────────────────────────────

    def record_assessment(
        self,
        concept_id: str,
        event_type: str,
        session_id: str | None = None,
        context: dict | None = None,
    ) -> Any:
        """Record a trusted assessment event (updates mastery)."""
        return self._temporal.record_event(
            concept_id=concept_id,
            event_type=event_type,
            session_id=session_id,
            context=context,
            evidence_type="assessment",
        )

    # ── Review Queue ──────────────────────────────────────────────────

    def get_review_queue(self, course_id: str | None = None) -> list[dict]:
        concepts = self._temporal.get_decaying_concepts(
            threshold=0.4, course_id=course_id,
        )
        return [
            {
                "concept_id": c.concept_id,
                "name": c.concept_name,
                "mastery": round(c.current_mastery, 3),
                "urgency": c.urgency,
                "days_since_review": round(c.days_since_review, 1),
            }
            for c in concepts
        ]

    # ── Internals ─────────────────────────────────────────────────────

    def _count_trusted_events(self, course_id: str | None) -> int:
        import sqlite3
        with sqlite3.connect(str(self._store.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            if course_id:
                return conn.execute(
                    """SELECT COUNT(*) FROM learning_events
                       WHERE trust_status='trusted' AND concept_id IN
                       (SELECT concept_id FROM concepts WHERE course_id=?)""",
                    (course_id,),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM learning_events WHERE trust_status='trusted'"
            ).fetchone()[0]
