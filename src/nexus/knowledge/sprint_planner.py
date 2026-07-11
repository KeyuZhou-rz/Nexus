"""
Sprint Planner — Nexus Phase N (Memory Layer 2)

考前 14 天冲刺计划生成器。结合章节权重 + 掌握度 + 每日可用时间，
生成每日冲刺计划（带激将法挑战）。LLM 增强路径优先，规则兜底。

与 StudyPlanner 的差异:
- StudyPlanner: 通用周计划，学术中性语气
- SprintPlanner: 考前冲刺，激将法语气，章节权重约束，14 天窗口

用法:
    store = SQLiteStore(Path("data/nexus.db"))
    planner = SprintPlanner(store)
    plan = planner.generate(
        course_id="CS202_OS",
        exam_date="2026-07-15",
        chapter_weights={"ch3": 0.15, "ch7": 0.20, "ch9": 0.30},
        daily_hours=2.0,
    )
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .graph_retriever import GraphRetriever
from .prompts import SPRINT_PLAN_PROMPT
from .sqlite_store import SQLiteStore
from .study_planner import (
    _build_prereq_chain_text,
    _compute_prereq_depths,
    _filter_concepts_with_chunk_evidence,
)
from .temporal_memory import TemporalMemory

logger = logging.getLogger(__name__)


@dataclass
class _SprintConcept:
    concept_id: str
    name: str
    mastery: float
    prereq_depth: int
    review_count: int
    chapter: str | None = None
    chapter_weight: float = 0.0


class SprintPlanner:
    """考前冲刺计划生成器。"""

    SPRINT_WINDOW_DAYS: int = 14

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        llm_client: Any = None,
    ) -> None:
        self._store = sqlite_store
        self._temporal = TemporalMemory(sqlite_store)
        self._graph = GraphRetriever(sqlite_store)
        self._llm = llm_client

    def is_sprint_mode(self, exam_date: str) -> bool:
        """判断是否进入冲刺模式（距离考试 ≤14 天）。"""
        try:
            exam_dt = datetime.fromisoformat(exam_date)
            days_until = (exam_dt - datetime.now()).days
            return 0 < days_until <= self.SPRINT_WINDOW_DAYS
        except (ValueError, TypeError):
            return False

    def generate(
        self,
        course_id: str,
        exam_date: str,
        chapter_weights: dict[str, float] | None = None,
        daily_hours: float = 2.0,
    ) -> dict[str, Any]:
        """
        生成考前冲刺计划。

        参数:
        - course_id: 目标课程
        - exam_date: 考试日期（ISO 格式）
        - chapter_weights: 章节权重映射，如 {"ch3": 0.15, "ch7": 0.20}
        - daily_hours: 每日可用复习时间（小时）

        返回:
        {mode, exam_date, days_until_exam, plan: [{day, date, focus, rationale, challenge, estimated_minutes}], method}
        """
        # Compute days until exam
        try:
            exam_dt = datetime.fromisoformat(exam_date)
            days_until_exam = max(1, (exam_dt - datetime.now()).days)
        except (ValueError, TypeError):
            return {
                "mode": "error",
                "message": f"Invalid exam date: {exam_date}",
                "plan": [],
            }

        if days_until_exam > self.SPRINT_WINDOW_DAYS:
            return {
                "mode": "relaxed",
                "message": (
                    f"距离考试还有 {days_until_exam} 天（超过 {self.SPRINT_WINDOW_DAYS} 天窗口），"
                    "建议使用 StudyPlanner.generate() 生成常规周计划。"
                ),
                "exam_date": exam_date,
                "days_until_exam": days_until_exam,
                "plan": [],
            }

        if not chapter_weights:
            return {
                "mode": "unavailable",
                "message": "Sprint planning requires explicit chapter weights.",
                "exam_date": exam_date,
                "days_until_exam": days_until_exam,
                "plan": [],
            }

        # Gather data
        concepts_raw = _filter_concepts_with_chunk_evidence(
            self._store.get_concepts_by_course(course_id), self._store
        )
        if not concepts_raw:
            return self._empty_plan(course_id, exam_date, days_until_exam)

        snapshots = self._temporal.get_all_mastery(course_id)
        mastery_by_id = {s.concept_id: s.current_mastery for s in snapshots}
        depths = _compute_prereq_depths(concepts_raw, self._store)

        # Build concept objects with optional chapter assignment
        concepts: list[_SprintConcept] = []
        for c in concepts_raw:
            cid = c["concept_id"]
            mastery = mastery_by_id.get(cid, 0.0)
            depth = depths.get(cid, 0)
            review_count = next(
                (s.review_count for s in snapshots if s.concept_id == cid), 0
            )
            # Try to detect chapter from concept name or topic metadata
            chapter = self._guess_chapter(c)
            ch_weight = chapter_weights.get(chapter, 0.0) if chapter else 0.0
            concepts.append(_SprintConcept(
                concept_id=cid,
                name=c.get("name", cid),
                mastery=mastery,
                prereq_depth=depth,
                review_count=review_count,
                chapter=chapter,
                chapter_weight=ch_weight,
            ))

        # Try LLM path first
        if self._llm:
            try:
                return self._llm_sprint(
                    course_id=course_id,
                    exam_date=exam_date,
                    days_until_exam=days_until_exam,
                    concepts=concepts,
                    concepts_raw=concepts_raw,
                    chapter_weights=chapter_weights,
                    daily_hours=daily_hours,
                )
            except Exception as exc:
                logger.warning(f"LLM sprint plan failed, falling back to rule: {exc}")

        return self._rule_sprint(
            course_id=course_id,
            exam_date=exam_date,
            days_until_exam=days_until_exam,
            concepts=concepts,
            chapter_weights=chapter_weights,
            daily_hours=daily_hours,
        )

    # ── Rule-based sprint ────────────────────────────────────────

    def _rule_sprint(
        self,
        course_id: str,
        exam_date: str,
        days_until_exam: int,
        concepts: list[_SprintConcept],
        chapter_weights: dict[str, float],
        daily_hours: float,
    ) -> dict[str, Any]:
        today = datetime.now()
        num_days = min(days_until_exam, self.SPRINT_WINDOW_DAYS)
        urgency_factor = 14.0 / max(1, days_until_exam)

        scored: list[tuple[float, _SprintConcept]] = []
        for c in concepts:
            ch_bonus = 1.0 + c.chapter_weight * 2.0  # high-weight chapters get boosted
            priority = (1.0 - c.mastery) * (1.0 + c.prereq_depth * 0.3) * urgency_factor * ch_bonus
            scored.append((priority, c))

        scored.sort(key=lambda x: (-x[0], x[1].prereq_depth))

        # Find weakest concept for daily challenge
        weakest = min(concepts, key=lambda c: c.mastery)

        per_day = max(1, len(scored) // num_days)
        daily_plans: list[dict] = []
        for day_idx in range(num_days):
            start = day_idx * per_day
            end = start + per_day if day_idx < num_days - 1 else len(scored)
            day_concepts = scored[start:end]
            focus = [c.name for _, c in day_concepts]

            # Daily challenge: pick the weakest concept assigned today
            if day_concepts:
                day_weakest = min(day_concepts, key=lambda x: x[1].mastery)
                challenge_concept = day_weakest[1]
                challenge = (
                    f"我赌你今天会在'{challenge_concept.name}'上丢分——"
                    f"掌握度只有 {challenge_concept.mastery*100:.0f}%"
                    + (f"，但这章占考试 {challenge_concept.chapter_weight*100:.0f}% 的分值" if challenge_concept.chapter_weight > 0 else "")
                    + "。敢不敢做一道题验证一下？"
                )
            else:
                challenge_concept = weakest
                challenge = (
                    f"我赌你今天会在'{challenge_concept.name}'上丢分。"
                    f"敢不敢做一道题验证一下？"
                )

            if day_idx == 0:
                rationale = f"冲刺第一天，从你最薄弱的环节开刀。别逃避，直面它！"
            elif day_idx == num_days - 1:
                rationale = "最后一天，来场模拟考。把之前赌你丢分的地方再过一遍，看看还敢不敢错！"
            else:
                rationale = f"继续推进。进度条不会自己走，还有 {num_days - day_idx - 1} 天，别松懈。"

            minutes = self._estimate_minutes(day_concepts, daily_hours)
            daily_plans.append({
                "day": day_idx + 1,
                "date": (today + timedelta(days=day_idx)).strftime("%Y-%m-%d"),
                "focus": focus,
                "rationale": rationale,
                "challenge": challenge,
                "estimated_minutes": minutes,
            })

        return {
            "course_id": course_id,
            "mode": "sprint",
            "exam_date": exam_date,
            "days_until_exam": days_until_exam,
            "plan": daily_plans,
            "method": "rule",
        }

    # ── LLM-enhanced sprint ──────────────────────────────────────

    def _llm_sprint(
        self,
        course_id: str,
        exam_date: str,
        days_until_exam: int,
        concepts: list[_SprintConcept],
        concepts_raw: list[dict],
        chapter_weights: dict[str, float],
        daily_hours: float,
    ) -> dict[str, Any]:
        if not self._llm:
            raise RuntimeError("LLM client not available")

        today = datetime.now()
        num_days = min(days_until_exam, self.SPRINT_WINDOW_DAYS)

        # Build mastery table
        mastery_lines = [
            f"  {c.name}: {c.mastery:.0%} (复习{c.review_count}次)"
            + (f", 章节 {c.chapter}" if c.chapter else "")
            + (f" [占比 {c.chapter_weight*100:.0f}%]" if c.chapter_weight > 0 else "")
            for c in sorted(concepts, key=lambda c: c.mastery)
        ]
        mastery_table = "\n".join(mastery_lines) if mastery_lines else "（无概念数据）"

        # Build chapter weights text
        if chapter_weights:
            ch_lines = [f"  {ch}: {wt*100:.0f}%" for ch, wt in sorted(chapter_weights.items(), key=lambda x: -x[1])]
            chapter_text = "\n".join(ch_lines)
        else:
            chapter_text = "（未提供章节权重，请均匀分配）"

        # Build prerequisite chain text
        prereq_text = _build_prereq_chain_text(concepts_raw, self._store)

        prompt = SPRINT_PLAN_PROMPT.format(
            course_id=course_id,
            exam_date=exam_date,
            days_until_exam=days_until_exam,
            chapter_weights=chapter_text,
            mastery_table=mastery_table,
            daily_hours=daily_hours,
        )

        messages = [
            {"role": "system", "content": "你是一个考前冲刺教练。用激将法激励学生。只输出 JSON。"},
            {"role": "user", "content": prompt},
        ]

        output = self._llm.chat(messages, temperature=0.3)
        data = json.loads(output.content)
        llm_plan = data.get("plan", [])

        daily_plans: list[dict] = []
        for i, entry in enumerate(llm_plan[:num_days]):
            daily_plans.append({
                "day": entry.get("day", i + 1),
                "date": (today + timedelta(days=i)).strftime("%Y-%m-%d"),
                "focus": entry.get("focus", []),
                "rationale": entry.get("rationale", ""),
                "challenge": entry.get("challenge", ""),
                "estimated_minutes": entry.get("estimated_minutes", 45),
            })

        # Fill remaining days
        while len(daily_plans) < num_days:
            idx = len(daily_plans)
            daily_plans.append({
                "day": idx + 1,
                "date": (today + timedelta(days=idx)).strftime("%Y-%m-%d"),
                "focus": [],
                "rationale": "自由复习 + 查漏补缺",
                "challenge": "最后的机会了，别留遗憾！",
                "estimated_minutes": int(daily_hours * 60 * 0.5),
            })

        return {
            "course_id": course_id,
            "mode": "sprint",
            "exam_date": exam_date,
            "days_until_exam": days_until_exam,
            "plan": daily_plans,
            "method": "llm",
        }

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _estimate_minutes(
        day_concepts: list[tuple[float, "_SprintConcept"]],
        daily_hours: float,
    ) -> int:
        """
        当天预计学习时长：随当天分配的概念数量 + 平均掌握度差距（越弱的概念
        需要越多时间）伸缩，而不是固定按 daily_hours 的 75%（与概念数量/
        难度无关，冲刺后期概念变少时明显估多了）。
        始终封顶在当天实际可用时间（daily_hours*60）内。
        """
        available = max(1, int(daily_hours * 60))
        if not day_concepts:
            return int(available * 0.5)

        avg_gap = sum(1.0 - c.mastery for _, c in day_concepts) / len(day_concepts)
        per_concept_minutes = 10 + 15 * avg_gap  # 10~25 分钟/概念，取决于掌握度差距
        raw = per_concept_minutes * len(day_concepts)
        return max(20, min(available, int(raw)))

    @staticmethod
    def _guess_chapter(concept: dict) -> str | None:
        """尝试从概念数据推断章节标识。"""
        topic = concept.get("topic", "")
        name = concept.get("name", "")
        lecture = concept.get("lecture_number")
        text = f"{topic} {name}".lower()

        # 匹配 "ch3" / "chapter 3" 以及 lecture/lec/unit/week 等常见命名，
        # 统一归到同一个 "chN" 章节桶（与 lecture_number 字段的映射方式一致）。
        import re
        m = re.search(r'(?:ch(?:apter)?|lecture|lec|unit|week)[\s._-]*(\d+)', text)
        if m:
            return f"ch{m.group(1)}"
        if lecture is not None:
            return f"ch{lecture}"
        return None

    @staticmethod
    def _empty_plan(course_id: str, exam_date: str, days_until_exam: int) -> dict[str, Any]:
        today = datetime.now()
        num_days = min(days_until_exam, SprintPlanner.SPRINT_WINDOW_DAYS)
        return {
            "course_id": course_id,
            "mode": "sprint",
            "exam_date": exam_date,
            "days_until_exam": days_until_exam,
            "plan": [
                {
                    "day": d + 1,
                    "date": (today + timedelta(days=d)).strftime("%Y-%m-%d"),
                    "focus": [],
                    "rationale": "该课程无已知概念，请先上传课件素材",
                    "challenge": "",
                    "estimated_minutes": 0,
                }
                for d in range(num_days)
            ],
            "method": "rule",
        }
