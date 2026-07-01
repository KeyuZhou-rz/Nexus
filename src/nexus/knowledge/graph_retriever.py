"""
Graph-Augmented Retriever — Nexus Phase 4A

给定 QueryEngine 返回的 chunk_ids，遍历概念图谱找到:
1. 这些 chunks 涉及的概念（query_concepts）
2. 这些概念的前置知识（prerequisites）
3. 同课程内的相关概念（related_same_course）
4. 跨课程的类比/关联概念（related_cross_course）

输出 GraphContext 供 context_assembler 注入到 LLM prompt 中。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────

@dataclass
class RelatedConcept:
    """通过图谱遍历发现的关联概念。"""
    concept_id: str
    name: str
    course_id: str
    definition: str | None
    relation_type: str          # 与 query concept 的关系类型
    relation_to: str            # 关联到哪个 query concept（name）
    confidence: float
    is_cross_course: bool


@dataclass
class GraphContext:
    """图谱遍历的完整输出，供 context_assembler 使用。"""
    query_concepts: list[dict[str, str]]        # chunks 直接关联的概念 [{name, course_id}]
    prerequisites: list[RelatedConcept]         # 前置知识
    related_same_course: list[RelatedConcept]   # 同课程相关概念
    related_cross_course: list[RelatedConcept]  # 跨课程关联（核心差异化能力）

    @property
    def has_content(self) -> bool:
        return bool(self.prerequisites or self.related_same_course or self.related_cross_course)


# ────────────────────────────────────────────────
# 主类
# ────────────────────────────────────────────────

class GraphRetriever:
    """
    遍历概念图谱，为检索结果补充结构化上下文。

    参数:
    - sqlite_store: SQLiteStore 实例（含 Phase 4 表）
    - max_related: 每类关联概念的最大返回数
    """

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        max_related: int = 5,
    ) -> None:
        self._store = sqlite_store
        self._max_related = max_related

    def retrieve(
        self,
        chunk_ids: list[str],
        course_id: str | None = None,
    ) -> GraphContext:
        """
        给定检索到的 chunk_ids，遍历图谱返回 GraphContext。

        流程:
        1. chunk_ids → chunk_concepts → 关联概念列表
        2. 对每个概念查 concept_relations:
           - prerequisite 边 → prerequisites 列表
           - 同课程其他关系 → related_same_course
           - 跨课程关系 → related_cross_course
        3. 去重 + 按 confidence 排序 + 截断到 max_related
        """
        # Step 1: 找到 chunks 关联的概念
        concept_rows = self._store.get_concepts_for_chunks(chunk_ids)

        if not concept_rows:
            return GraphContext(
                query_concepts=[],
                prerequisites=[],
                related_same_course=[],
                related_cross_course=[],
            )

        query_concepts = [
            {"name": c["name"], "course_id": c["course_id"], "concept_id": c["concept_id"]}
            for c in concept_rows
        ]

        # 去重概念 ID
        seen_concept_ids = {c["concept_id"] for c in concept_rows}

        prerequisites: list[RelatedConcept] = []
        related_same: list[RelatedConcept] = []
        related_cross: list[RelatedConcept] = []

        # Step 2: 对每个 query concept 查关系
        for qc in concept_rows:
            qc_id = qc["concept_id"]
            qc_name = qc["name"]
            qc_course = qc["course_id"]

            relations = self._store.get_relations_for_concept(qc_id)

            for rel in relations:
                other_id = (
                    rel["target_id"] if rel.get("direction") == "outgoing"
                    else rel["source_id"]
                )

                # 跳过已在 query_concepts 中的概念
                if other_id in seen_concept_ids:
                    continue

                other_name = rel.get("other_name", "")
                other_course = rel.get("other_course", "")
                other_def = rel.get("other_definition")
                rel_type = rel["relation_type"]
                confidence = rel.get("confidence", 0.5)
                is_cross = other_course != qc_course

                rc = RelatedConcept(
                    concept_id=other_id,
                    name=other_name,
                    course_id=other_course,
                    definition=other_def,
                    relation_type=rel_type,
                    relation_to=qc_name,
                    confidence=confidence,
                    is_cross_course=is_cross,
                )

                # 分类
                if rel_type == "prerequisite" and rel.get("direction") == "outgoing":
                    # qc → other 是 prerequisite，即 other 是 qc 的前置知识
                    prerequisites.append(rc)
                elif is_cross:
                    related_cross.append(rc)
                else:
                    related_same.append(rc)

                seen_concept_ids.add(other_id)

        # Step 3: 排序 + 截断
        prerequisites.sort(key=lambda x: x.confidence, reverse=True)
        related_same.sort(key=lambda x: x.confidence, reverse=True)
        related_cross.sort(key=lambda x: x.confidence, reverse=True)

        ctx = GraphContext(
            query_concepts=[{"name": c["name"], "course_id": c["course_id"]} for c in query_concepts],
            prerequisites=prerequisites[: self._max_related],
            related_same_course=related_same[: self._max_related],
            related_cross_course=related_cross[: self._max_related],
        )

        if ctx.has_content:
            logger.info(
                f"GraphRetriever: {len(ctx.prerequisites)} prereqs, "
                f"{len(ctx.related_same_course)} same-course, "
                f"{len(ctx.related_cross_course)} cross-course"
            )

        return ctx
