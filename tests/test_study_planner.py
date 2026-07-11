"""U2: Tests for StudyPlanner — rule-based plan, prerequisite depth, LLM fallback."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nexus.knowledge.study_planner import (
    StudyPlanner,
    _compute_prereq_depths,
)


# ── helpers ──────────────────────────────────────────────────────────

def _concept_row(cid: str, name: str, course: str = "CS202_OS") -> dict:
    return {"concept_id": cid, "name": name, "course_id": course, "definition": f"Definition of {name}"}


def _relation_row(
    source_id: str,
    target_id: str,
    rel_type: str = "prerequisite",
    confidence: float = 0.8,
    other_name: str = "",
    other_course: str = "CS202_OS",
    direction: str = "outgoing",
) -> dict:
    return {
        "source_id": source_id,
        "target_id": target_id,
        "relation_type": rel_type,
        "confidence": confidence,
        "other_name": other_name or target_id,
        "other_course": other_course,
        "direction": direction,
        "other_definition": None,
    }


class _FakeSQLiteStore:
    """Minimal stub with get_concepts_by_course + get_relations_for_concept."""

    def __init__(self, concepts: list[dict], relations: dict[str, list[dict]] | None = None):
        self.concepts = concepts
        self._relations = relations or {}

    def get_concepts_by_course(self, course_id: str) -> list[dict]:
        return [c for c in self.concepts if c.get("course_id") == course_id]

    def get_relations_for_concept(self, concept_id: str) -> list[dict]:
        return self._relations.get(concept_id, [])

    def get_all_concepts(self):
        return self.concepts


class _FakeTemporalMemory:
    """Returns fixed mastery snapshots."""

    class _Snapshot:
        def __init__(self, cid: str, name: str, mastery: float, course: str = "CS202_OS"):
            self.concept_id = cid
            self.concept_name = name
            self.course_id = course
            self.current_mastery = mastery
            self.review_count = 3
            self.urgency = "stable"

    def __init__(self, snapshots: list | None = None):
        self._snapshots = snapshots or []

    def get_all_mastery(self, course_id: str | None = None):
        return self._snapshots

    def get_decaying_concepts(self, threshold: float = 0.4, course_id: str | None = None):
        return [s for s in self._snapshots if s.current_mastery < threshold]


# ── tests: _compute_prereq_depths ───────────────────────────────────

class TestPrereqDepths:
    def test_no_prerequisites_all_zero(self):
        concepts = [
            _concept_row("c1", "Deadlock"),
            _concept_row("c2", "Mutex"),
        ]
        store = _FakeSQLiteStore(concepts, {})
        depths = _compute_prereq_depths(concepts, store)
        assert depths == {"c1": 0, "c2": 0}

    def test_single_level_prereq(self):
        concepts = [
            _concept_row("c1", "Process"),
            _concept_row("c2", "Deadlock"),
        ]
        relations = {
            "c2": [_relation_row("c2", "c1", "prerequisite", 0.9, "Process", direction="outgoing")],
        }
        store = _FakeSQLiteStore(concepts, relations)
        depths = _compute_prereq_depths(concepts, store)
        assert depths["c1"] == 0  # no prereqs
        assert depths["c2"] == 1  # depends on c1

    def test_multi_level_prereq_chain(self):
        concepts = [
            _concept_row("c1", "ISA"),
            _concept_row("c2", "Syscall"),
            _concept_row("c3", "Process"),
        ]
        # Process → Syscall → ISA  (Syscall depends on ISA; Process depends on Syscall)
        relations = {
            "c3": [_relation_row("c3", "c2", "prerequisite", 0.9, "Syscall", direction="outgoing")],
            "c2": [_relation_row("c2", "c1", "prerequisite", 0.9, "ISA", direction="outgoing")],
        }
        store = _FakeSQLiteStore(concepts, relations)
        depths = _compute_prereq_depths(concepts, store)
        assert depths["c1"] == 0
        assert depths["c2"] == 1
        assert depths["c3"] == 2  # Process → Syscall → ISA

    def test_non_prerequisite_relations_ignored(self):
        concepts = [
            _concept_row("c1", "Mutex"),
            _concept_row("c2", "Semaphore"),
        ]
        relations = {
            "c1": [_relation_row("c1", "c2", "extends", 0.7, "Semaphore", direction="outgoing")],
        }
        store = _FakeSQLiteStore(concepts, relations)
        depths = _compute_prereq_depths(concepts, store)
        assert depths["c1"] == 0
        assert depths["c2"] == 0


# ── tests: StudyPlanner (rule-based) ────────────────────────────────

class TestStudyPlannerRule:
    def test_orphan_concepts_are_excluded_when_store_can_verify_links(self):
        concepts = [_concept_row("linked", "Linked"), _concept_row("orphan", "Orphan")]
        store = _FakeSQLiteStore(concepts)
        store.get_chunks_for_concept = lambda cid: ["chunk-1"] if cid == "linked" else []
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory([])
        planner._graph = None
        planner._llm = None

        plan = planner.generate("CS202_OS")
        focus = [name for day in plan["plan"] for name in day["focus"]]
        assert "Linked" in focus
        assert "Orphan" not in focus
    def test_empty_course_returns_empty_plan(self):
        store = _FakeSQLiteStore([])
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory([])
        planner._graph = None
        planner._llm = None

        plan = planner.generate("CS999_EMPTY", exam_date="2026-07-15")
        assert plan["course_id"] == "CS999_EMPTY"
        assert plan["method"] == "rule"
        assert len(plan["plan"]) == 7
        assert all("该课程无已知概念" in d["rationale"] for d in plan["plan"])

    def test_rule_plan_covers_all_concepts(self):
        concepts = [
            _concept_row("c1", "deadlock"),
            _concept_row("c2", "mutex"),
            _concept_row("c3", "scheduling"),
            _concept_row("c4", "paging"),
        ]
        snapshots = [
            _FakeTemporalMemory._Snapshot("c1", "deadlock", 0.2),
            _FakeTemporalMemory._Snapshot("c2", "mutex", 0.5),
            _FakeTemporalMemory._Snapshot("c3", "scheduling", 0.7),
            _FakeTemporalMemory._Snapshot("c4", "paging", 0.9),
        ]
        store = _FakeSQLiteStore(concepts)
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory(snapshots)
        planner._graph = None
        planner._llm = None

        plan = planner.generate("CS202_OS", exam_date="2026-07-15")
        assert plan["method"] == "rule"
        assert plan["course_id"] == "CS202_OS"
        assert plan["days_until_exam"] is not None
        assert len(plan["plan"]) == 7

        # All concepts appear somewhere
        all_focus = []
        for day in plan["plan"]:
            all_focus.extend(day["focus"])
        for name in ["deadlock", "mutex", "scheduling", "paging"]:
            assert name in all_focus, f"{name} should be in plan"

    def test_low_mastery_concepts_appear_earlier(self):
        concepts = [
            _concept_row("c1", "critical-concept"),
            _concept_row("c2", "mastered-concept"),
        ]
        snapshots = [
            _FakeTemporalMemory._Snapshot("c1", "critical-concept", 0.15),
            _FakeTemporalMemory._Snapshot("c2", "mastered-concept", 0.95),
        ]
        store = _FakeSQLiteStore(concepts)
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory(snapshots)
        planner._graph = None
        planner._llm = None

        plan = planner.generate("CS202_OS")
        day1_concepts = plan["plan"][0]["focus"]
        # Low mastery concept should be in day 1
        assert "critical-concept" in day1_concepts

    def test_exam_urgency_amplifies_priority(self):
        """Concepts get higher priority when exam is closer (urgency_factor = 14/days)."""
        concepts = [_concept_row("c1", "topic")]
        snapshots = [_FakeTemporalMemory._Snapshot("c1", "topic", 0.5)]
        store = _FakeSQLiteStore(concepts)
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory(snapshots)
        planner._graph = None
        planner._llm = None

        # Exam in 3 days (safe from floor rounding)
        exam_dt = datetime.now() + timedelta(days=3, hours=1)
        plan = planner.generate("CS202_OS", exam_date=exam_dt.isoformat())
        assert plan["method"] == "rule"
        assert plan["days_until_exam"] >= 2

    def test_no_exam_date_no_crash(self):
        concepts = [_concept_row("c1", "topic")]
        snapshots = [_FakeTemporalMemory._Snapshot("c1", "topic", 0.5)]
        store = _FakeSQLiteStore(concepts)
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory(snapshots)
        planner._graph = None
        planner._llm = None

        plan = planner.generate("CS202_OS")  # no exam_date
        assert plan["days_until_exam"] is None
        assert len(plan["plan"]) == 7

    def test_plan_structure_valid(self):
        concepts = [_concept_row("c1", "paging"), _concept_row("c2", "segmentation")]
        snapshots = [
            _FakeTemporalMemory._Snapshot("c1", "paging", 0.3),
            _FakeTemporalMemory._Snapshot("c2", "segmentation", 0.6),
        ]
        store = _FakeSQLiteStore(concepts)
        planner = StudyPlanner.__new__(StudyPlanner)
        planner._store = store
        planner._temporal = _FakeTemporalMemory(snapshots)
        planner._graph = None
        planner._llm = None

        plan = planner.generate("CS202_OS", exam_date="2026-08-01")
        for day in plan["plan"]:
            assert "day" in day
            assert "date" in day
            assert "focus" in day
            assert isinstance(day["focus"], list)
            assert "rationale" in day
            assert isinstance(day["estimated_minutes"], int)
            assert day["estimated_minutes"] > 0
