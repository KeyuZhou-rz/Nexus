"""
Temporal Memory — Nexus Phase 4B

基于 Ebbinghaus 遗忘曲线追踪每个概念的掌握度:
    mastery(t) = initial_mastery * e^(-decay_rate * days_since_review)

事件类型对参数的影响:
    correct_answer → initial_mastery += 0.15, decay_rate *= 0.85 (巩固)
    struggled      → initial_mastery += 0.05, decay_rate *= 1.1  (部分学习)
    reviewed       → 仅记录 exposure，不作为掌握证据
    forgot         → initial_mastery *= 0.5,  decay_rate *= 1.2  (显著衰减)
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


# 事件对 mastery 参数的影响规则
_EVENT_EFFECTS: dict[str, dict[str, float]] = {
    "correct_answer": {"mastery_add": 0.15, "decay_mult": 0.85, "streak_add": 1},
    "struggled":      {"mastery_add": 0.05, "decay_mult": 1.10, "streak_add": 0},
    "forgot":         {"mastery_mult": 0.50, "decay_mult": 1.20, "streak_add": -999},
}


@dataclass
class MasterySnapshot:
    """某概念在某时刻的掌握度快照。"""
    concept_id: str
    concept_name: str
    course_id: str
    current_mastery: float      # 0.0-1.0，实时计算
    days_since_review: float
    decay_rate: float
    review_count: int
    streak: int
    urgency: str                # critical | decaying | stable | mastered
    initial_mastery: float = 0.0  # 最近一次复习后的起始掌握度（未衰减）

    @staticmethod
    def classify_urgency(mastery: float) -> str:
        if mastery >= 0.8:
            return "mastered"
        if mastery >= 0.5:
            return "stable"
        if mastery >= 0.3:
            return "decaying"
        return "critical"


class TemporalMemory:
    """
    管理所有概念的 Ebbinghaus 遗忘曲线。

    参数:
    - sqlite_store: SQLiteStore 实例（含 learning_events / concept_mastery 表）
    """

    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._store = sqlite_store

    def record_event(
        self,
        concept_id: str,
        event_type: str,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
        evidence_type: str = "exposure",
    ) -> MasterySnapshot | None:
        """
        记录一次学习事件，更新掌握度参数，返回更新后的快照。

        信任层级 (Phase 4D-3):
          - assessment → 更新 mastery (经可信评估验证)
          - direct     → 更新 mastery (用户明确反馈)
          - exposure   → 仅记录，不修改 mastery
          - legacy     → 仅记录，不修改 mastery

        如果 concept_id 不存在于 concepts 表则跳过。
        """
        concept = self._store.get_concept(concept_id)
        if not concept:
            return None

        trust = str((context or {}).get("trust_status") or "legacy")
        ev_type = str((context or {}).get("evidence_type") or evidence_type)

        if event_type == "reviewed":
            self._store.record_learning_event(
                concept_id=concept_id,
                event_type=event_type,
                session_id=session_id,
                context=context,
                evidence_type="exposure",
                trust_status="legacy",
            )
            return None

        # Phase 4D-3: only trusted evidence modifies mastery.
        # Passive QA, reading, and model inference do not alter mastery.
        _TRUSTED_EVIDENCE = {"assessment", "direct", "user_self_report", "instructor_feedback"}
        if ev_type not in _TRUSTED_EVIDENCE:
            self._store.record_learning_event(
                concept_id=concept_id,
                event_type=event_type,
                session_id=session_id,
                context=context,
                evidence_type=ev_type,
                trust_status="legacy",
            )
            return None

        effects = _EVENT_EFFECTS.get(event_type)
        if not effects:
            logger.warning(f"未知事件类型: {event_type}")
            return None

        mastery = self._store.record_mastery_event_atomic(
            concept_id=concept_id,
            event_type=event_type,
            session_id=session_id,
            context=context,
            evidence_type=ev_type,
            mastery_add=effects.get("mastery_add", 0.0),
            mastery_mult=effects.get("mastery_mult", 1.0),
            decay_mult=effects["decay_mult"],
            streak_add=int(effects.get("streak_add", 0)),
        )
        initial = mastery["initial_mastery"]
        decay = mastery["decay_rate"]
        count = mastery["review_count"]
        streak = mastery["streak"]

        return MasterySnapshot(
            concept_id=concept_id,
            concept_name=concept["name"],
            course_id=concept["course_id"],
            current_mastery=initial,
            days_since_review=0.0,
            decay_rate=decay,
            review_count=count,
            streak=streak,
            urgency=MasterySnapshot.classify_urgency(initial),
            initial_mastery=initial,
        )

    def get_mastery(self, concept_id: str) -> float:
        """
        计算概念当前的实时掌握度。
        mastery(t) = initial_mastery * e^(-decay_rate * days_since_review)
        """
        row = self._store.get_mastery(concept_id)
        if not row or not row.get("last_reviewed"):
            return 0.0

        days = self._days_since(row["last_reviewed"])
        return row["initial_mastery"] * math.exp(-row["decay_rate"] * days)

    def get_all_mastery(
        self,
        course_id: str | None = None,
    ) -> list[MasterySnapshot]:
        """获取所有（或指定课程的）概念的掌握度快照。"""
        rows = self._store.get_all_mastery(course_id)
        snapshots = []
        for r in rows:
            days = self._days_since(r["last_reviewed"]) if r.get("last_reviewed") else 999.0
            current = r["initial_mastery"] * math.exp(-r["decay_rate"] * days)
            current = max(0.0, min(1.0, current))
            snapshots.append(MasterySnapshot(
                concept_id=r["concept_id"],
                concept_name=r.get("name", ""),
                course_id=r.get("course_id", ""),
                current_mastery=current,
                days_since_review=days,
                decay_rate=r["decay_rate"],
                review_count=r["review_count"],
                streak=r["streak"],
                urgency=MasterySnapshot.classify_urgency(current),
                initial_mastery=r["initial_mastery"],
            ))
        return snapshots

    def get_decaying_concepts(
        self,
        threshold: float = 0.4,
        course_id: str | None = None,
    ) -> list[MasterySnapshot]:
        """获取掌握度已衰减到阈值以下的概念（曾经学过但快忘了）。"""
        all_mastery = self.get_all_mastery(course_id)
        return [
            s for s in all_mastery
            if s.current_mastery < threshold and s.review_count > 0
        ]

    @staticmethod
    def _days_since(timestamp_str: str) -> float:
        """计算从 timestamp 到现在经过的天数。"""
        try:
            ts = datetime.fromisoformat(timestamp_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - ts).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            return 999.0
