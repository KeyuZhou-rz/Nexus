"""
Memory API — Nexus Phase N4

记忆系统的统一入口。聚合 TemporalMemory + UserProfile + StudyPlanner + UnifiedMemoryReader，
为 Web 前端和 CLI 提供单一 API 边界。

用法:
    store = SQLiteStore(Path("data/nexus.db"))
    mem = MemoryAPI(store)

    # 掌握度
    overview = mem.get_mastery_overview(course_id="CS202_OS")
    decaying = mem.get_decaying_concepts(course_id="CS202_OS")

    # 学习事件
    mem.record_event("cs202_os__deadlock", "correct_answer")

    # 学习计划
    plan = mem.generate_study_plan(course_id="CS202_OS", exam_date="2026-07-15")

    # 上下文（供简报注入）
    topics = mem.get_context_topics()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .sqlite_store import SQLiteStore
from .temporal_memory import TemporalMemory, MasterySnapshot
from .user_profile import UserProfileManager
from .study_planner import StudyPlanner
from .unified_memory import UnifiedMemoryReader

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 聚合数据结构
# ────────────────────────────────────────────────

@dataclass
class MasteryOverview:
    """课程掌握度总览。"""
    course_id: str
    total_concepts: int
    mastered: int       # mastery >= 0.8
    stable: int         # 0.5 <= mastery < 0.8
    decaying: int       # 0.3 <= mastery < 0.5
    critical: int       # mastery < 0.3
    average_mastery: float
    concepts: list[MasterySnapshot] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_id": self.course_id,
            "total_concepts": self.total_concepts,
            "mastered": self.mastered,
            "stable": self.stable,
            "decaying": self.decaying,
            "critical": self.critical,
            "average_mastery": round(self.average_mastery, 3),
            "concepts": [
                {
                    "concept_id": c.concept_id,
                    "concept_name": c.concept_name,
                    "current_mastery": round(c.current_mastery, 3),
                    "days_since_review": round(c.days_since_review, 1),
                    "urgency": c.urgency,
                }
                for c in self.concepts
            ],
        }


@dataclass
class ForgettingAlert:
    """预测性遗忘告警。"""
    concept_id: str
    concept_name: str
    current_mastery: float
    days_until_critical: float  # 预计 N 天后降到 critical 阈值以下
    urgency: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "concept_name": self.concept_name,
            "current_mastery": round(self.current_mastery, 3),
            "days_until_critical": round(self.days_until_critical, 1),
            "urgency": self.urgency,
        }


@dataclass
class MemoryContext:
    """简报/学习上下文注入数据。"""
    weak_points: list[str]
    review_queue: list[str]
    low_mastery_topics: list[str]
    context_topics: list[str]       # 按优先级合并，最多 5 个

    def to_dict(self) -> dict[str, Any]:
        return {
            "weak_points": self.weak_points,
            "review_queue": self.review_queue,
            "low_mastery_topics": self.low_mastery_topics,
            "context_topics": self.context_topics,
        }


# ────────────────────────────────────────────────
# Memory API
# ────────────────────────────────────────────────

class MemoryAPI:
    """
    记忆系统统一入口。

    参数:
    - store: SQLiteStore 实例
    """

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store
        self._temporal = TemporalMemory(store)
        self._profile = UserProfileManager(store)
        self._planner = StudyPlanner(store)
        self._reader = UnifiedMemoryReader(store)

    # ════════════════════════════════════════════════
    # 掌握度（TemporalMemory）
    # ════════════════════════════════════════════════

    def get_mastery(self, concept_id: str) -> float:
        """获取单个概念的实时掌握度 (0.0-1.0)。"""
        return self._temporal.get_mastery(concept_id)

    def get_mastery_overview(
        self, course_id: str | None = None
    ) -> MasteryOverview:
        """获取课程掌握度总览（分类统计 + 概念列表）。"""
        snaps = self._temporal.get_all_mastery(course_id)
        total = len(snaps)
        mastered = sum(1 for s in snaps if s.urgency == "mastered")
        stable = sum(1 for s in snaps if s.urgency == "stable")
        decaying = sum(1 for s in snaps if s.urgency == "decaying")
        critical = sum(1 for s in snaps if s.urgency == "critical")
        avg = sum(s.current_mastery for s in snaps) / max(total, 1)

        return MasteryOverview(
            course_id=course_id or "all",
            total_concepts=total,
            mastered=mastered,
            stable=stable,
            decaying=decaying,
            critical=critical,
            average_mastery=avg,
            concepts=snaps,
        )

    def get_decaying_concepts(
        self,
        threshold: float = 0.4,
        course_id: str | None = None,
    ) -> list[MasterySnapshot]:
        """获取掌握度已衰减到阈值以下的概念。"""
        return self._temporal.get_decaying_concepts(threshold=threshold, course_id=course_id)

    def record_event(
        self,
        concept_id: str,
        event_type: str,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> MasterySnapshot | None:
        """记录学习事件，更新掌握度。"""
        return self._temporal.record_event(
            concept_id=concept_id,
            event_type=event_type,
            session_id=session_id,
            context=context,
        )

    def get_forgetting_alerts(
        self, course_id: str | None = None
    ) -> list[ForgettingAlert]:
        """生成预测性遗忘告警：未来 7 天内可能降到 critical 的概念。"""
        snaps = self._temporal.get_all_mastery(course_id)
        alerts: list[ForgettingAlert] = []
        for s in snaps:
            if s.days_since_review <= 0 or s.decay_rate <= 0:
                continue
            # 解 mastery(t) = 0.3 求 t：t = -ln(0.3 / initial) / decay_rate
            # 然后减去已过去的天数
            if s.current_mastery <= 0.3:
                days_until = 0.0
            else:
                try:
                    t_critical = -__import__("math").log(0.3 / s.current_mastery) / s.decay_rate
                    days_until = max(0.0, t_critical - s.days_since_review)
                except (ValueError, ZeroDivisionError):
                    continue
            if days_until <= 7:
                alerts.append(ForgettingAlert(
                    concept_id=s.concept_id,
                    concept_name=s.concept_name,
                    current_mastery=s.current_mastery,
                    days_until_critical=days_until,
                    urgency="imminent" if days_until <= 2 else "soon",
                ))
        return sorted(alerts, key=lambda a: a.days_until_critical)

    # ════════════════════════════════════════════════
    # 用户画像（UserProfileManager）
    # ════════════════════════════════════════════════

    def get_profile(self) -> dict[str, Any]:
        """获取完整用户画像 dict。"""
        return self._profile.get()

    def get_weak_points(self) -> list[dict[str, str]]:
        """获取弱项列表 [{"concept": ..., "course": ...}]。"""
        return self._profile.get_weak_points()

    def get_learning_style(self) -> str:
        """获取学习风格（visual|textual|example-driven|default）。"""
        return self._profile.get_learning_style()

    def get_courses(self) -> list[str]:
        """获取已注册课程列表。"""
        return self._profile.get_courses()

    # ════════════════════════════════════════════════
    # 学习计划（StudyPlanner）
    # ════════════════════════════════════════════════

    def generate_study_plan(
        self,
        course_id: str,
        exam_date: str | None = None,
        num_days: int = 7,
    ) -> dict[str, Any]:
        """生成特定期限的学习计划。"""
        return self._planner.generate(
            course_id=course_id,
            exam_date=exam_date,
            num_days=num_days,
        )

    # ════════════════════════════════════════════════
    # 上下文（UnifiedMemoryReader）
    # ════════════════════════════════════════════════

    def get_context_topics(self) -> list[str]:
        """按优先级合并弱项+待复习+低掌握度，返回最多 5 个 topic。"""
        return self._reader.get_context_topics()

    def get_memory_context(self) -> MemoryContext:
        """获取完整的记忆上下文（供简报/QA 注入）。"""
        return MemoryContext(
            weak_points=self._reader.get_weak_points(),
            review_queue=self._reader.get_review_queue(),
            low_mastery_topics=self._reader.get_low_mastery_topics(),
            context_topics=self._reader.get_context_topics(),
        )
