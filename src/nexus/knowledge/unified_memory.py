"""
UnifiedMemoryReader — Nexus Phase M1-7

从 SQLite 知识图谱中读取记忆状态，替代旧 state.json 读取。
供 briefing._inject_learning_context 等消费方使用。

数据来源:
- weak_points: UserProfileManager.get() → weak_points 列表
- review_queue: TemporalMemory.get_decaying_concepts() → 快遗忘的概念名
- low_mastery_topics: TemporalMemory.get_all_mastery() → mastery≤0.5 的概念名
"""
from __future__ import annotations

from .sqlite_store import SQLiteStore
from .user_profile import UserProfileManager
from .temporal_memory import TemporalMemory


class UnifiedMemoryReader:
    """
    统一记忆读取器。从 SQLite 知识图谱读取用户记忆状态。

    用法:
        store = SQLiteStore(Path("data/nexus.db"))
        reader = UnifiedMemoryReader(store)
        weak_points = reader.get_weak_points()
        review_queue = reader.get_review_queue()
        low_mastery = reader.get_low_mastery_topics()
    """

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store
        self._profile_mgr = UserProfileManager(store)
        self._temporal = TemporalMemory(store)

    def get_weak_points(self) -> list[str]:
        """从 user_profile 获取弱项概念名列表（归一化小写）。"""
        profile = self._profile_mgr.get()
        weak_points = profile.get("weak_points", [])
        return [
            wp.get("concept", "").strip().lower()
            for wp in weak_points
            if wp.get("concept", "").strip()
        ]

    def get_review_queue(self, threshold: float = 0.4) -> list[str]:
        """
        获取掌握度已衰减到阈值以下、需要复习的概念名列表。
        来自 TemporalMemory 的 Ebbinghaus 衰减计算。
        """
        decaying = self._temporal.get_decaying_concepts(threshold=threshold)
        return [s.concept_name.strip().lower() for s in decaying if s.concept_name.strip()]

    def get_low_mastery_topics(self, max_mastery: float = 0.5) -> list[str]:
        """
        获取掌握度低于指定值的概念名列表。
        来自 TemporalMemory 的 concept_mastery 表。
        """
        all_mastery = self._temporal.get_all_mastery()
        return [
            s.concept_name.strip().lower()
            for s in all_mastery
            if s.current_mastery <= max_mastery and s.concept_name.strip()
        ]

    def get_context_topics(self) -> list[str]:
        """
        便捷方法：按优先级合并弱项 + 待复习 + 低掌握度。
        返回最多 5 个 topic 名称。
        """
        topics: list[str] = []
        seen: set[str] = set()

        for topic in self.get_review_queue()[:2]:
            if topic not in seen:
                topics.append(topic)
                seen.add(topic)

        for topic in self.get_low_mastery_topics()[:3]:
            if topic not in seen:
                topics.append(topic)
                seen.add(topic)

        for topic in self.get_weak_points()[:3]:
            if topic not in seen:
                topics.append(topic)
                seen.add(topic)

        return topics[:5]
