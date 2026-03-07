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

from datetime import datetime
from typing import Any

from .sqlite_store import SQLiteStore

# Profile 的 SQLite key（user_profile 表中的主键）
_PROFILE_KEY = "profile"


class UserProfileManager:
    """
    User Profile 的高层管理器。
    所有操作直接读写 SQLite user_profile 表，保证持久化。

    参数：
    - store: SQLiteStore 实例，负责实际存储
    """

    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    # ────────────────────────────────────────────────
    # 读取
    # ────────────────────────────────────────────────

    def get(self) -> dict[str, Any]:
        """获取完整 profile。不存在时返回默认空结构。"""
        profile = self._store.get_profile(_PROFILE_KEY)
        return _ensure_defaults(profile)

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
        新增薄弱知识点。若已存在相同 concept 则更新 last_seen。
        用于测试或手动标记，正式更新走 apply_patch()。
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

    def apply_patch(self, patch: dict[str, Any]) -> dict[str, str]:
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

        返回：{"applied": "N changes", "notes": "..."}
        """
        changes: list[str] = []

        # ── 新增薄弱点 ──
        for item in patch.get("add_weak_points") or []:
            if isinstance(item, dict) and item.get("concept"):
                self.add_weak_point(item["concept"], item.get("course", ""))
                changes.append(f"+weak_point:{item['concept']}")

        # ── 移除薄弱点（已掌握） ──
        for concept in patch.get("remove_weak_points") or []:
            if isinstance(concept, str) and concept:
                self.remove_weak_point(concept)
                changes.append(f"-weak_point:{concept}")

        # ── 标记掌握 ──
        for item in patch.get("add_mastered") or []:
            if isinstance(item, dict) and item.get("concept"):
                self.add_mastered(item["concept"], item.get("course", ""))
                changes.append(f"+mastered:{item['concept']}")

        # ── 更新学习风格 ──
        style = patch.get("update_learning_style")
        if style:
            self.update_learning_style(str(style))
            changes.append(f"learning_style→{style}")

        # ── 新增常见错误 ──
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
