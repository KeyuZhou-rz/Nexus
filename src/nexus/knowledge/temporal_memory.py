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
        evidence_type: str = "direct",
    ) -> MasterySnapshot | None:
        """
        记录一次学习事件，更新掌握度参数，返回更新后的快照。
        如果 concept_id 不存在于 concepts 表则跳过。
        """
        concept = self._store.get_concept(concept_id)
        if not concept:
            return None

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

        effects = _EVENT_EFFECTS.get(event_type)
        if not effects:
            logger.warning(f"未知事件类型: {event_type}")
            return None

        # 记录事件流水
        self._store.record_learning_event(
            concept_id=concept_id,
            event_type=event_type,
            session_id=session_id,
            context=context,
            evidence_type=str((context or {}).get("evidence_type") or evidence_type),
            trust_status="trusted",
        )

        # 读取当前 mastery 参数
        mastery_row = self._store.get_mastery(concept_id)
        if mastery_row:
            initial = mastery_row["initial_mastery"]
            decay = mastery_row["decay_rate"]
            count = mastery_row["review_count"]
            streak = mastery_row["streak"]
        else:
            initial = 0.0
            decay = 0.3
            count = 0
            streak = 0

        # 应用事件效果
        if "mastery_add" in effects:
            initial = min(1.0, initial + effects["mastery_add"])
        if "mastery_mult" in effects:
            initial = initial * effects["mastery_mult"]
        decay = max(0.01, min(1.0, decay * effects["decay_mult"]))
        count += 1

        streak_delta = effects.get("streak_add", 0)
        if streak_delta == -999:
            streak = 0
        else:
            streak = max(0, streak + int(streak_delta))

        # 持久化
        self._store.upsert_mastery(
            concept_id=concept_id,
            initial_mastery=initial,
            decay_rate=decay,
            review_count=count,
            streak=streak,
        )

        return MasterySnapshot(
            concept_id=concept_id,
            concept_name=concept["name"],
            course_id=concept["course_id"],
            current_mastery=initial,  # 刚复习过，mastery = initial
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
