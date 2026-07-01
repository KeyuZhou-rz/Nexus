"""
Study Planner — Nexus Phase U2

结合考试日期 + 掌握度 (TemporalMemory) + 前置关系 (GraphRetriever 拓扑序)
生成周学习计划。LLM 增强路径优先，规则兜底。

用法:
    store = SQLiteStore(Path("data/nexus.db"))
    planner = StudyPlanner(store)
    plan = planner.generate(course_id="CS202_OS", exam_date="2026-07-15")
    # plan["plan"] → 7-day daily breakdown
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .graph_retriever import GraphRetriever
from .sqlite_store import SQLiteStore
from .temporal_memory import TemporalMemory

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# Study Planner Prompt (LLM path)
# ────────────────────────────────────────────────

STUDY_PLAN_PROMPT = """\
你是一个学习规划专家。请根据以下信息为一名学生生成为期 7 天的学习计划。

## 课程
{course_id}

## 考试日期
{exam_date}
距离考试还有 {days_until_exam} 天。

## 当前日期
{today}

## 概念掌握度（0-100%，越低越需要复习）
{mastery_table}

## 概念前置关系（A → B 表示 A 是 B 的前置知识）
{prerequisite_chain}

## 要求
1. 按前置关系拓扑序安排：先复习前置概念，再深入依赖概念
2. 优先安排掌握度最低且考试最相关的概念
3. 每天安排 2-4 个概念，预估学习时间 30-60 分钟
4. 最后一天安排综合复习 + 查漏补缺
5. 给出每天的学习理由（rationale）

只输出 JSON:
{{
  "plan": [
    {{
      "day": 1,
      "focus": ["concept_name"],
      "rationale": "简要理由",
      "estimated_minutes": 45
    }}
  ]
}}
"""


# ────────────────────────────────────────────────
# Data structures
# ────────────────────────────────────────────────

@dataclass
class _ConceptWithDepth:
    concept_id: str
    name: str
    mastery: float          # 0-1
    prereq_depth: int       # 前置链深度（0 = 无前置）
    review_count: int


def _compute_prereq_depths(
    concepts: list[dict],
    store: SQLiteStore,
) -> dict[str, int]:
    """
    对每个概念计算前置链的最大深度（拓扑距离）。
    深度 0 = 无前置；深度 1 = 有直接前置；深度 2 = 前置有前置...
    使用迭代 BFS 避免递归爆栈。
    """
    depths: dict[str, int] = {}
    for c in concepts:
        cid = c["concept_id"]
        if cid in depths:
            continue
        # BFS from this concept following prerequisite edges
        visited: set[str] = {cid}
        frontier = [cid]
        depth = -1  # start at -1 so direct prereqs give depth 1
        while frontier:
            depth += 1
            next_frontier: list[str] = []
            for node_id in frontier:
                rels = store.get_relations_for_concept(node_id)
                for rel in rels:
                    if rel.get("relation_type") != "prerequisite":
                        continue
                    # prerequisite edge: node depends on target
                    if rel.get("direction") == "outgoing":
                        prereq_id = rel.get("target_id", "")
                    else:
                        prereq_id = rel.get("source_id", "")
                    if prereq_id and prereq_id not in visited:
                        visited.add(prereq_id)
                        next_frontier.append(prereq_id)
            frontier = next_frontier
        depths[cid] = max(0, depth)
    return depths


def _build_prereq_chain_text(
    concepts: list[dict],
    store: SQLiteStore,
) -> str:
    """Render prerequisite chains as human-readable text for the LLM prompt."""
    lines: list[str] = []
    for c in concepts:
        cid = c["concept_id"]
        name = c.get("name", cid)
        rels = store.get_relations_for_concept(cid)
        prereq_names: list[str] = []
        for rel in rels:
            if rel.get("relation_type") != "prerequisite":
                continue
            other = rel.get("other_name", rel.get("target_id", ""))
            if rel.get("direction") == "outgoing":
                prereq_names.append(other)
        if prereq_names:
            lines.append(f"  {name} ← 前置: {', '.join(prereq_names)}")
    return "\n".join(lines) if lines else "（无已知前置关系）"


# ────────────────────────────────────────────────
# StudyPlanner
# ────────────────────────────────────────────────

class StudyPlanner:
    """
    周学习计划生成器。

    参数:
    - sqlite_store: SQLiteStore 实例
    - llm_client: KnowledgeLLMClient（可选，LLM 增强路径）
    """

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        llm_client: Any = None,
    ) -> None:
        self._store = sqlite_store
        self._temporal = TemporalMemory(sqlite_store)
        self._graph = GraphRetriever(sqlite_store)
        self._llm = llm_client

    def generate(
        self,
        course_id: str,
        exam_date: str | None = None,
        num_days: int = 7,
    ) -> dict[str, Any]:
        """
        生成学习计划。

        参数:
        - course_id: 目标课程
        - exam_date: 考试日期（ISO 格式，如 "2026-07-15"）
        - num_days: 计划天数（默认 7）

        返回:
        {
            "course_id": str,
            "generated_at": ISO str,
            "exam_date": str | None,
            "days_until_exam": int | None,
            "plan": [{day, date, focus, rationale, estimated_minutes}, ...],
            "method": "llm" | "rule",
        }
        """
        # Gather data
        concepts_raw = self._store.get_concepts_by_course(course_id)
        if not concepts_raw:
            return self._empty_plan(course_id, exam_date, num_days)

        # Mastery and depth
        snapshots = self._temporal.get_all_mastery(course_id)
        mastery_by_id: dict[str, float] = {
            s.concept_id: s.current_mastery for s in snapshots
        }
        depths = _compute_prereq_depths(concepts_raw, self._store)

        # Build concept objects
        concepts: list[_ConceptWithDepth] = []
        for c in concepts_raw:
            cid = c["concept_id"]
            mastery = mastery_by_id.get(cid, 0.0)
            depth = depths.get(cid, 0)
            review_count = next(
                (s.review_count for s in snapshots if s.concept_id == cid), 0
            )
            concepts.append(_ConceptWithDepth(
                concept_id=cid,
                name=c.get("name", cid),
                mastery=mastery,
                prereq_depth=depth,
                review_count=review_count,
            ))

        # Compute exam urgency
        days_until_exam: int | None = None
        if exam_date:
            try:
                exam_dt = datetime.fromisoformat(exam_date)
                days_until_exam = max(1, (exam_dt - datetime.now()).days)
            except (ValueError, TypeError):
                days_until_exam = None

        # Try LLM path first
        if self._llm:
            try:
                return self._llm_plan(
                    course_id=course_id,
                    exam_date=exam_date,
                    days_until_exam=days_until_exam,
                    concepts=concepts,
                    concepts_raw=concepts_raw,
                    num_days=num_days,
                )
            except Exception as exc:
                logger.warning(f"LLM study plan failed, falling back to rule: {exc}")

        # Rule-based fallback
        return self._rule_plan(
            course_id=course_id,
            exam_date=exam_date,
            days_until_exam=days_until_exam,
            concepts=concepts,
            num_days=num_days,
        )

    # ── Rule-based plan ──────────────────────────────────────────────

    def _rule_plan(
        self,
        course_id: str,
        exam_date: str | None,
        days_until_exam: int | None,
        concepts: list[_ConceptWithDepth],
        num_days: int,
    ) -> dict[str, Any]:
        """Priority-sorted rule-based plan: (1-mastery) × depth_bonus × urgency."""
        today = datetime.now()

        # Compute priority score
        urgency_factor = 1.0
        if days_until_exam:
            urgency_factor = 14.0 / max(1, days_until_exam)  # higher when closer

        scored: list[tuple[float, _ConceptWithDepth]] = []
        for c in concepts:
            priority = (1.0 - c.mastery) * (1.0 + c.prereq_depth * 0.3) * urgency_factor
            scored.append((priority, c))

        # Sort by priority descending, but respect prereq depth within same day
        scored.sort(key=lambda x: (-x[0], x[1].prereq_depth))

        # Distribute across days
        per_day = max(1, len(scored) // num_days)
        daily_plans: list[dict] = []
        for day_idx in range(num_days):
            start = day_idx * per_day
            end = start + per_day if day_idx < num_days - 1 else len(scored)
            day_concepts = scored[start:end]

            focus = [c.name for _, c in day_concepts]
            if day_idx == 0:
                rationale = "从最薄弱且无前置依赖的概念入手，建立基础"
            elif day_idx == num_days - 1:
                rationale = "综合复习，回顾本周所有概念，查漏补缺"
            else:
                rationale = "按优先级继续推进，巩固已学并引入相关概念"

            daily_plans.append({
                "day": day_idx + 1,
                "date": (today + timedelta(days=day_idx)).strftime("%Y-%m-%d"),
                "focus": focus,
                "rationale": rationale,
                "estimated_minutes": min(60, 30 + len(focus) * 10),
            })

        return {
            "course_id": course_id,
            "generated_at": today.isoformat(),
            "exam_date": exam_date,
            "days_until_exam": days_until_exam,
            "plan": daily_plans,
            "method": "rule",
        }

    # ── LLM-enhanced plan ───────────────────────────────────────────

    def _llm_plan(
        self,
        course_id: str,
        exam_date: str | None,
        days_until_exam: int | None,
        concepts: list[_ConceptWithDepth],
        concepts_raw: list[dict],
        num_days: int,
    ) -> dict[str, Any]:
        """Use LLM to generate a study plan with natural-language rationale."""
        if not self._llm:
            raise RuntimeError("LLM client not available")

        today = datetime.now()

        # Build mastery table
        mastery_lines = [
            f"  {c.name}: {c.mastery:.0%} (复习{c.review_count}次, 前置深度{c.prereq_depth})"
            for c in sorted(concepts, key=lambda c: c.mastery)
        ]
        mastery_table = "\n".join(mastery_lines) if mastery_lines else "（无概念数据）"

        # Build prerequisite chain text
        prereq_text = _build_prereq_chain_text(concepts_raw, self._store)

        prompt = STUDY_PLAN_PROMPT.format(
            course_id=course_id,
            exam_date=exam_date or "未设定",
            days_until_exam=days_until_exam or "未知",
            today=today.strftime("%Y-%m-%d"),
            mastery_table=mastery_table,
            prerequisite_chain=prereq_text,
        )

        messages = [
            {"role": "system", "content": "You are a study planner. Return JSON only."},
            {"role": "user", "content": prompt},
        ]

        output = self._llm.chat(messages, temperature=0.3)
        data = json.loads(output.content)
        llm_plan = data.get("plan", [])

        # Merge LLM days with date stamps
        daily_plans: list[dict] = []
        for i, day_entry in enumerate(llm_plan[:num_days]):
            daily_plans.append({
                "day": day_entry.get("day", i + 1),
                "date": (today + timedelta(days=i)).strftime("%Y-%m-%d"),
                "focus": day_entry.get("focus", []),
                "rationale": day_entry.get("rationale", ""),
                "estimated_minutes": day_entry.get("estimated_minutes", 45),
            })

        # If LLM returned fewer days, fill remaining with review days
        while len(daily_plans) < num_days:
            idx = len(daily_plans)
            daily_plans.append({
                "day": idx + 1,
                "date": (today + timedelta(days=idx)).strftime("%Y-%m-%d"),
                "focus": [],
                "rationale": "自由复习 + 查漏补缺",
                "estimated_minutes": 30,
            })

        return {
            "course_id": course_id,
            "generated_at": today.isoformat(),
            "exam_date": exam_date,
            "days_until_exam": days_until_exam,
            "plan": daily_plans,
            "method": "llm",
        }

    # ── Empty plan ──────────────────────────────────────────────────

    @staticmethod
    def _empty_plan(course_id: str, exam_date: str | None, num_days: int) -> dict[str, Any]:
        today = datetime.now()
        return {
            "course_id": course_id,
            "generated_at": today.isoformat(),
            "exam_date": exam_date,
            "days_until_exam": None,
            "plan": [
                {
                    "day": d + 1,
                    "date": (today + timedelta(days=d)).strftime("%Y-%m-%d"),
                    "focus": [],
                    "rationale": "该课程无已知概念，请先上传课件素材",
                    "estimated_minutes": 0,
                }
                for d in range(num_days)
            ],
            "method": "rule",
        }
