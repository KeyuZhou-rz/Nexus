"""
User Profile 管理 — Nexus Phase 3

高层封装，构建于 SQLiteStore.get_profile() / set_profile() 之上。

Profile 数据结构（存入 SQLite user_profile 表）：
{
  "courses": ["CS202_OS", "CS305_Database"],       # 用户注册的课程列表
  "weak_points": [                                  # 薄弱知识点，按 last_seen 排序
    {"concept": "死锁检测", "course": "CS202", "last_seen": "2026-03-08"}
  ],
  "mastered": [                                     # 已掌握的知识点
    {"concept": "Binary Search", "course": "CS101", "confirmed_at": "2026-03-01"}
  ],
  "learning_style": "visual",                       # visual|textual|example-driven
  "preferred_language": "zh-CN",
  "common_mistakes": ["混淆 mutex 和 semaphore"]
}

设计原则：
- 所有写操作均为原子事务（通过 SQLiteStore 实现）
- apply_patch() 是唯一对外的批量写入接口，由 SessionManager 调用
- NEVER per-turn 更新，ONLY session 粒度更新
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .concept_resolver import ConceptResolver
from .sqlite_store import SQLiteStore
from .temporal_memory import TemporalMemory

logger = logging.getLogger(__name__)

# Profile 的 SQLite key（user_profile 表中的主键）
_PROFILE_KEY = "profile"


class UserProfileManager:
    """
    User Profile 的高层管理器。
    所有操作直接读写 SQLite user_profile 表，保证持久化。

    参数：
    - store: SQLiteStore 实例，负责实际存储
    - temporal_memory: 若提供，get() 返回的 weak_points/mastered 改为从
      TemporalMemory.concept_mastery 实时派生（唯一权威源），不再读取本地
      JSON 里的自由文本字段；apply_patch() 也会经 concept_resolver 解析后
      写入 TemporalMemory，而不是写自由文本。
      不提供时（默认）保持旧行为，纯 JSON 存储/读取，向后兼容。
    - concept_resolver: 配合 temporal_memory 使用，将自由文本概念名解析为
      规范 concept_id。
    """

    def __init__(
        self,
        store: SQLiteStore,
        temporal_memory: TemporalMemory | None = None,
        concept_resolver: ConceptResolver | None = None,
    ) -> None:
        self._store = store
        self._temporal = temporal_memory
        self._resolver = concept_resolver

    # ────────────────────────────────────────────────
    # 读取
    # ────────────────────────────────────────────────

    def get(self) -> dict[str, Any]:
        """获取完整 profile。不存在时返回默认空结构。
        若已绑定 temporal_memory，weak_points/mastered 会被 TemporalMemory
        派生出的实时数据覆盖（唯一权威源，避免与自由文本记录互相矛盾）。
        """
        profile = self._store.get_profile(_PROFILE_KEY)
        profile = _ensure_defaults(profile)
        if self._temporal is not None:
            profile["weak_points"], profile["mastered"] = self._derive_mastery_lists()
        return profile

    def _derive_mastery_lists(self) -> tuple[list[dict], list[dict]]:
        """从 TemporalMemory.concept_mastery 派生 weak_points / mastered。"""
        snapshots = self._temporal.get_all_mastery()

        weak = [
            {
                "concept": s.concept_name,
                "course": s.course_id,
                "mastery": round(s.current_mastery, 2),
                "urgency": s.urgency,
            }
            for s in snapshots
            if s.urgency in ("critical", "decaying")
        ]
        weak.sort(key=lambda w: w["mastery"])

        mastered = [
            {
                "concept": s.concept_name,
                "course": s.course_id,
                "mastery": round(s.current_mastery, 2),
            }
            for s in snapshots
            if s.urgency == "mastered"
        ]
        mastered.sort(key=lambda m: -m["mastery"])

        return weak[:30], mastered

    def get_courses(self) -> list[str]:
        """获取用户注册的课程列表。"""
        return self.get().get("courses") or []

    def get_weak_points(self) -> list[dict[str, str]]:
        """获取薄弱知识点列表，每项包含 concept / course / last_seen。"""
        return self.get().get("weak_points") or []

    def get_learning_style(self) -> str:
        """获取学习风格（visual|textual|example-driven），默认 default。"""
        return self.get().get("learning_style") or "default"

    # ────────────────────────────────────────────────
    # 原子写操作
    # ────────────────────────────────────────────────

    def set_courses(self, course_ids: list[str]) -> None:
        """覆盖课程列表（用于初始化或课程变更）。"""
        profile = self.get()
        profile["courses"] = list(dict.fromkeys(course_ids))  # 去重保序
        self._store.set_profile(profile, _PROFILE_KEY)

    def add_course(self, course_id: str) -> None:
        """追加一门课程（已存在则忽略）。"""
        profile = self.get()
        courses: list[str] = profile.get("courses") or []
        if course_id not in courses:
            courses.append(course_id)
            profile["courses"] = courses
            self._store.set_profile(profile, _PROFILE_KEY)

    def add_weak_point(self, concept: str, course: str) -> None:
        """
        新增薄弱知识点（自由文本写入 JSON）。
        当 profile_mgr 绑定了 temporal_memory 时，get() 会用 TemporalMemory
        派生的数据覆盖这里写入的内容——本方法仍保留给未绑定 temporal_memory
        的调用方（如 memory_writer.py 的 resolver 桥接路径）使用。
        """
        profile = self.get()
        weak_points: list[dict] = profile.get("weak_points") or []
        now = datetime.now().date().isoformat()

        for wp in weak_points:
            if wp.get("concept") == concept:
                wp["last_seen"] = now
                self._store.set_profile(profile, _PROFILE_KEY)
                return

        weak_points.append({"concept": concept, "course": course, "last_seen": now})
        # 按 last_seen 倒序，最新的放最前
        weak_points.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
        profile["weak_points"] = weak_points[:30]  # 最多保留 30 个薄弱点
        self._store.set_profile(profile, _PROFILE_KEY)

    def remove_weak_point(self, concept: str) -> None:
        """从薄弱点列表移除某概念（用户反馈已掌握）。"""
        profile = self.get()
        weak_points: list[dict] = profile.get("weak_points") or []
        profile["weak_points"] = [wp for wp in weak_points if wp.get("concept") != concept]
        self._store.set_profile(profile, _PROFILE_KEY)

    def add_mastered(self, concept: str, course: str) -> None:
        """标记某概念已掌握，同时从薄弱点移除。"""
        profile = self.get()
        mastered: list[dict] = profile.get("mastered") or []
        now = datetime.now().date().isoformat()
        # 去重：相同 concept 则更新 confirmed_at
        mastered = [m for m in mastered if m.get("concept") != concept]
        mastered.append({"concept": concept, "course": course, "confirmed_at": now})
        profile["mastered"] = mastered

        # 同步从薄弱点移除
        profile["weak_points"] = [
            wp for wp in (profile.get("weak_points") or [])
            if wp.get("concept") != concept
        ]
        self._store.set_profile(profile, _PROFILE_KEY)

    def update_learning_style(self, style: str) -> None:
        """
        更新学习风格。
        合法值：visual | textual | example-driven
        其他值静默忽略（防止 LLM 幻觉写入脏数据）。
        """
        valid = {"visual", "textual", "example-driven"}
        if style not in valid:
            return
        profile = self.get()
        profile["learning_style"] = style
        self._store.set_profile(profile, _PROFILE_KEY)

    def add_common_mistake(self, mistake: str) -> None:
        """追加常见错误描述（去重，最多保留 20 条）。"""
        profile = self.get()
        mistakes: list[str] = profile.get("common_mistakes") or []
        if mistake not in mistakes:
            mistakes.append(mistake)
            profile["common_mistakes"] = mistakes[-20:]
        self._store.set_profile(profile, _PROFILE_KEY)

    # ────────────────────────────────────────────────
    # LLM Patch 应用（Session Manager 调用的主入口）
    # ────────────────────────────────────────────────

    def apply_patch(
        self,
        patch: dict[str, Any],
        course_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, str]:
        """
        将 LLM 生成的 JSON patch 原子应用到 profile。

        patch 格式（来自 PROFILE_UPDATE_PROMPT 输出）：
        {
          "add_weak_points": [{"concept": "...", "course": "..."}],
          "remove_weak_points": ["concept_name"],
          "add_mastered": [{"concept": "...", "course": "..."}],
          "update_learning_style": null | "visual|textual|example-driven",
          "add_common_mistakes": ["..."],
          "notes": "变更说明"
        }

        当绑定了 temporal_memory + concept_resolver 时：
        - add_weak_points / remove_weak_points / add_mastered 不再写自由文本，
          而是用 course_id（调用方传入的 session 真实 course_id，而非 patch
          里 LLM 猜的 course 字段——那是不可靠的自由文本）+ concept_resolver
          解析出 concept_id，再调用 TemporalMemory.record_event()。
        - 解析失败（未知概念/无 course_id）时跳过该条，不再退化为自由文本写入，
          避免重新引入两套数据源不同步的问题。
        - 解析成功以外的字段（学习风格、常见错误）行为不变。

        未绑定 temporal_memory/concept_resolver 时（如未传 course_id），保持
        旧行为：直接写自由文本到 weak_points/mastered。

        返回：{"applied": "N changes", "notes": "..."}
        """
        changes: list[str] = []
        can_resolve = (
            self._temporal is not None and self._resolver is not None and bool(course_id)
        )

        # ── 新增薄弱点 ──
        for item in patch.get("add_weak_points") or []:
            if not (isinstance(item, dict) and item.get("concept")):
                continue
            concept = item["concept"]
            if can_resolve:
                resolution = self._resolver.resolve(concept, course_id)
                if resolution:
                    self._temporal.record_event(
                        resolution.concept_id, "struggled", session_id
                    )
                    changes.append(f"+weak_point:{resolution.name}")
                else:
                    logger.debug(
                        f"apply_patch: 无法解析薄弱点概念 {concept!r}（course={course_id!r}），已跳过"
                    )
            else:
                self.add_weak_point(concept, item.get("course", ""))
                changes.append(f"+weak_point:{concept}")

        # ── 移除薄弱点（视为该概念有所进步） ──
        for concept in patch.get("remove_weak_points") or []:
            if not (isinstance(concept, str) and concept):
                continue
            if can_resolve:
                resolution = self._resolver.resolve(concept, course_id)
                if resolution:
                    self._temporal.record_event(
                        resolution.concept_id, "correct_answer", session_id
                    )
                    changes.append(f"-weak_point:{resolution.name}")
                else:
                    logger.debug(
                        f"apply_patch: 无法解析待移除薄弱点 {concept!r}（course={course_id!r}），已跳过"
                    )
            else:
                self.remove_weak_point(concept)
                changes.append(f"-weak_point:{concept}")

        # ── 标记掌握 ──
        for item in patch.get("add_mastered") or []:
            if not (isinstance(item, dict) and item.get("concept")):
                continue
            concept = item["concept"]
            if can_resolve:
                resolution = self._resolver.resolve(concept, course_id)
                if resolution:
                    self._temporal.record_event(
                        resolution.concept_id, "correct_answer", session_id
                    )
                    changes.append(f"+mastered:{resolution.name}")
                else:
                    logger.debug(
                        f"apply_patch: 无法解析已掌握概念 {concept!r}（course={course_id!r}），已跳过"
                    )
            else:
                self.add_mastered(concept, item.get("course", ""))
                changes.append(f"+mastered:{concept}")

        # ── 更新学习风格（与掌握度无关，逻辑不变） ──
        style = patch.get("update_learning_style")
        if style:
            self.update_learning_style(str(style))
            changes.append(f"learning_style→{style}")

        # ── 新增常见错误（与掌握度无关，逻辑不变） ──
        for mistake in patch.get("add_common_mistakes") or []:
            if isinstance(mistake, str) and mistake:
                self.add_common_mistake(mistake)
                changes.append(f"+mistake:{mistake[:30]}")

        notes = str(patch.get("notes") or "")
        return {
            "applied": f"{len(changes)} changes",
            "details": changes,
            "notes": notes,
        }


# ────────────────────────────────────────────────
# 辅助
# ────────────────────────────────────────────────

def _ensure_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    """确保 profile 有所有必要字段（防止旧数据缺字段导致 KeyError）。"""
    defaults: dict[str, Any] = {
        "courses": [],
        "weak_points": [],
        "mastered": [],
        "learning_style": "default",
        "preferred_language": "zh-CN",
        "common_mistakes": [],
    }
    for key, val in defaults.items():
        if key not in profile:
            profile[key] = val
    return profile
