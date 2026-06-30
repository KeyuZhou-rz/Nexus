"""
Concept Extractor — Nexus Phase 4A

三阶段概念提取:
  Pass 1 (规则提取): 从 ParsedChunk 的 keywords/prerequisites/topic 中
         提取候选概念。零 LLM 成本，无 API key 时也可运行。
  Pass 2 (LLM 精炼): 合并重复概念、添加定义、发现课内关系。
         每个文档一次 LLM 调用。
  Pass 3 (跨课程关系): 新概念与已有图谱中其他课程概念的关联发现。
         新文档 ingest 后执行。

使用示例:
    extractor = ConceptExtractor(sqlite_store, llm_api_key="sk-xxx")
    candidates = extractor.extract_from_chunks(chunks, chunk_ids, "CS202_OS")
    refined, relations = extractor.refine_with_llm(candidates, chunks, chunk_ids, "CS202_OS")
    cross = extractor.discover_cross_course_relations(refined, "CS202_OS")
    relations.extend(cross)
    stats = extractor.persist(refined, relations)
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .parser import ParsedChunk
from .prompts import CONCEPT_EXTRACT_PROMPT, CROSS_COURSE_RELATION_PROMPT
from .sqlite_store import SQLiteStore

from .llm_gateway import gateway

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────

@dataclass
class ExtractedConcept:
    """从 chunk 中提取的概念。"""
    concept_id: str              # 规范化 slug: "cs202_os__deadlock"
    name: str                    # 显示名: "死锁 / Deadlock"
    course_id: str
    definition: str | None = None
    concept_type: str | None = None   # fundamental|applied|technique|theorem
    source_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class ExtractedRelation:
    """两个概念之间的关系。"""
    source_id: str
    target_id: str
    relation_type: str           # prerequisite|analogous_to|extends|applied_in|contradicts
    confidence: float = 1.0
    evidence: str | None = None


# ────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────

def normalize_concept_id(name: str, course_id: str) -> str:
    """
    生成规范化的概念 ID。
    'Deadlock Detection' + 'CS202_OS' -> 'cs202_os__deadlock_detection'
    '死锁检测' + 'CS202_OS' -> 'cs202_os__死锁检测'
    """
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", name.lower()).strip("_")
    course_prefix = re.sub(r"[^a-z0-9]+", "_", course_id.lower()).strip("_")
    return f"{course_prefix}__{slug}"


def _parse_llm_json(raw: str) -> Any:
    """从 LLM 输出中解析 JSON，处理 markdown 代码块包裹。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ────────────────────────────────────────────────
# 主类
# ────────────────────────────────────────────────

class ConceptExtractor:
    """
    概念提取器：从 ParsedChunks 中提取概念和关系，写入 SQLite 知识图谱。

    参数:
    - sqlite_store: SQLiteStore 实例
    - llm_api_key: QWEN API key（可选，无则跳过 Pass 2/3）
    """

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        llm_api_key: str | None = None,
    ) -> None:
        self._store = sqlite_store
        self._api_key = llm_api_key

    # ────────────────────────────────────────────────
    # Pass 1: 规则提取（零 LLM 成本）
    # ────────────────────────────────────────────────

    def extract_from_chunks(
        self,
        chunks: list[ParsedChunk],
        chunk_ids: list[str],
        course_id: str,
    ) -> list[ExtractedConcept]:
        """
        从现有 ParsedChunk 字段中规则提取候选概念。

        策略:
        - 每个 chunk.topic → 一个概念（最高质量来源）
        - chunk.keywords 中出现 ≥2 次的词 → 概念（高频=重要）
        - chunk.prerequisites 中每项 → 概念（可能无 source_chunk）
        - 去重后按 concept_id 合并
        """
        concept_map: dict[str, ExtractedConcept] = {}

        # 从 topics 提取（每个 chunk 的 topic 就是一个知识点标题）
        for chunk, cid in zip(chunks, chunk_ids):
            topic = chunk.topic.strip()
            if not topic or len(topic) < 2:
                continue
            concept_id = normalize_concept_id(topic, course_id)
            if concept_id in concept_map:
                concept_map[concept_id].source_chunk_ids.append(cid)
            else:
                concept_map[concept_id] = ExtractedConcept(
                    concept_id=concept_id,
                    name=topic,
                    course_id=course_id,
                    concept_type=self._infer_type(chunk.type),
                    source_chunk_ids=[cid],
                )

        # 从 keywords 提取（出现 ≥2 次的关键词）
        keyword_counter: Counter[str] = Counter()
        keyword_to_chunks: dict[str, list[str]] = {}
        for chunk, cid in zip(chunks, chunk_ids):
            for kw in chunk.keywords:
                kw = kw.strip()
                if len(kw) >= 2:
                    keyword_counter[kw] += 1
                    keyword_to_chunks.setdefault(kw, []).append(cid)

        for kw, count in keyword_counter.items():
            if count >= 2:
                concept_id = normalize_concept_id(kw, course_id)
                if concept_id not in concept_map:
                    concept_map[concept_id] = ExtractedConcept(
                        concept_id=concept_id,
                        name=kw,
                        course_id=course_id,
                        source_chunk_ids=keyword_to_chunks[kw][:5],
                    )

        # 从 prerequisites 提取（可能没有 source_chunk）
        for chunk, cid in zip(chunks, chunk_ids):
            for prereq in chunk.prerequisites:
                prereq = prereq.strip()
                if len(prereq) < 2:
                    continue
                concept_id = normalize_concept_id(prereq, course_id)
                if concept_id not in concept_map:
                    concept_map[concept_id] = ExtractedConcept(
                        concept_id=concept_id,
                        name=prereq,
                        course_id=course_id,
                    )

        logger.info(f"Pass 1 规则提取: {len(concept_map)} 个候选概念 ({course_id})")
        return list(concept_map.values())

    # ────────────────────────────────────────────────
    # Pass 2: LLM 精炼（每文档一次调用）
    # ────────────────────────────────────────────────

    def refine_with_llm(
        self,
        concepts: list[ExtractedConcept],
        chunks: list[ParsedChunk],
        chunk_ids: list[str],
        course_id: str,
    ) -> tuple[list[ExtractedConcept], list[ExtractedRelation]]:
        """
        用 LLM 精炼候选概念: 合并重复、添加定义、发现课内关系。
        无 API key 或 LLM 失败时返回原始候选概念 + 空关系。
        """
        if not self._api_key or not concepts:
            return concepts, []

        try:
            return self._call_refine_llm(concepts, chunks, chunk_ids, course_id)
        except Exception as exc:
            logger.warning(f"Pass 2 LLM 精炼失败，使用 Pass 1 结果: {exc}")
            return concepts, []

    def _call_refine_llm(
        self,
        concepts: list[ExtractedConcept],
        chunks: list[ParsedChunk],
        chunk_ids: list[str],
        course_id: str,
    ) -> tuple[list[ExtractedConcept], list[ExtractedRelation]]:
        """实际的 LLM 调用。"""
        import litellm

        # 构建 prompt 输入
        candidate_text = "\n".join(
            f"- {c.name}" + (f" ({c.concept_type})" if c.concept_type else "")
            for c in concepts[:50]  # 限制避免 prompt 过长
        )
        chunk_summaries = "\n".join(
            f"[{i}] {chunk.topic} | keywords: {', '.join(chunk.keywords[:6])}"
            for i, chunk in enumerate(chunks[:30])
        )

        prompt = CONCEPT_EXTRACT_PROMPT.format(
            course_id=course_id,
            candidate_concepts=candidate_text,
            chunk_summaries=chunk_summaries,
        )

        extra = gateway.litellm_kwargs.copy()
        if self._api_key:
            extra["api_key"] = self._api_key

        response = litellm.completion(
            model=gateway.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
            **extra,
        )

        result = _parse_llm_json(response.choices[0].message.content)

        # 解析 LLM 输出为 ExtractedConcept 列表
        refined: list[ExtractedConcept] = []
        name_to_concept: dict[str, ExtractedConcept] = {}

        for item in result.get("concepts", []):
            name = item.get("name", "").strip()
            if not name:
                continue
            concept_id = normalize_concept_id(name, course_id)
            # 关联回原始 chunk_ids
            source_indices = item.get("source_chunk_indices", [])
            source_cids = [
                chunk_ids[i] for i in source_indices
                if isinstance(i, int) and 0 <= i < len(chunk_ids)
            ]
            # 如果 LLM 没给 source_chunk_indices，尝试从 Pass 1 继承
            if not source_cids:
                for c in concepts:
                    if c.name == name or c.concept_id == concept_id:
                        source_cids = c.source_chunk_ids
                        break

            ec = ExtractedConcept(
                concept_id=concept_id,
                name=name,
                course_id=course_id,
                definition=item.get("definition"),
                concept_type=item.get("type"),
                source_chunk_ids=source_cids,
            )
            refined.append(ec)
            name_to_concept[name] = ec

        # 解析关系
        relations: list[ExtractedRelation] = []
        for rel in result.get("relations", []):
            src_name = rel.get("source", "").strip()
            tgt_name = rel.get("target", "").strip()
            rel_type = rel.get("type", "").strip()
            if not (src_name and tgt_name and rel_type):
                continue
            src_id = normalize_concept_id(src_name, course_id)
            tgt_id = normalize_concept_id(tgt_name, course_id)
            relations.append(ExtractedRelation(
                source_id=src_id,
                target_id=tgt_id,
                relation_type=rel_type,
                confidence=float(rel.get("confidence", 0.8)),
                evidence=rel.get("evidence"),
            ))

        # 保留 Pass 1 中有但 LLM 没返回的概念（LLM 可能遗漏）
        refined_ids = {c.concept_id for c in refined}
        for c in concepts:
            if c.concept_id not in refined_ids:
                refined.append(c)

        logger.info(
            f"Pass 2 LLM 精炼: {len(refined)} 概念, {len(relations)} 课内关系 ({course_id})"
        )
        return refined, relations

    # ────────────────────────────────────────────────
    # Pass 3: 跨课程关系发现
    # ────────────────────────────────────────────────

    def discover_cross_course_relations(
        self,
        new_concepts: list[ExtractedConcept],
        course_id: str,
    ) -> list[ExtractedRelation]:
        """
        将新概念与其他课程的已有概念比较，发现跨课程关系。
        无 API key、无其他课程概念、或 LLM 失败时返回空列表。
        """
        if not self._api_key or not new_concepts:
            return []

        # 加载其他课程的全部概念
        all_concepts = self._store.get_all_concepts()
        existing = [c for c in all_concepts if c["course_id"] != course_id]

        if not existing:
            logger.debug("无其他课程概念，跳过 Pass 3")
            return []

        try:
            return self._call_cross_course_llm(new_concepts, existing, course_id)
        except Exception as exc:
            logger.warning(f"Pass 3 跨课程关系发现失败: {exc}")
            return []

    def _call_cross_course_llm(
        self,
        new_concepts: list[ExtractedConcept],
        existing: list[dict[str, Any]],
        course_id: str,
    ) -> list[ExtractedRelation]:
        """跨课程关系的 LLM 调用。"""
        import litellm

        # 格式化新概念
        new_text = "\n".join(
            f"- {c.name}" + (f": {c.definition}" if c.definition else "")
            for c in new_concepts[:30]
        )

        # 格式化已有概念（按课程分组）
        by_course: dict[str, list[str]] = {}
        for c in existing[:100]:  # 限制总量
            cid = c["course_id"]
            line = f"  - {c['name']}" + (f": {c['definition']}" if c.get("definition") else "")
            by_course.setdefault(cid, []).append(line)

        existing_text = "\n".join(
            f"[{cid}]\n" + "\n".join(lines)
            for cid, lines in by_course.items()
        )

        prompt = CROSS_COURSE_RELATION_PROMPT.format(
            new_course_id=course_id,
            new_concepts=new_text,
            existing_concepts=existing_text,
        )

        extra = gateway.litellm_kwargs.copy()
        if self._api_key:
            extra["api_key"] = self._api_key

        response = litellm.completion(
            model=gateway.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024,
            response_format={"type": "json_object"},
            **extra,
        )

        raw = _parse_llm_json(response.choices[0].message.content)
        items = raw if isinstance(raw, list) else raw.get("relations", [])

        # 构建 name→concept_id 查找表
        new_name_map = {c.name: c.concept_id for c in new_concepts}
        existing_name_map = {c["name"]: c["concept_id"] for c in existing}

        relations: list[ExtractedRelation] = []
        for item in items:
            src_name = item.get("source", "").strip()
            tgt_name = item.get("target", "").strip()
            rel_type = item.get("type", "").strip()
            confidence = float(item.get("confidence", 0.7))

            if confidence < 0.7:
                continue

            # 查找 concept_id（优先精确匹配，否则规范化）
            src_id = new_name_map.get(src_name) or normalize_concept_id(src_name, course_id)
            tgt_course = item.get("target_course", "")
            tgt_id = existing_name_map.get(tgt_name)
            if not tgt_id and tgt_course:
                tgt_id = normalize_concept_id(tgt_name, tgt_course)
            if not tgt_id:
                continue

            relations.append(ExtractedRelation(
                source_id=src_id,
                target_id=tgt_id,
                relation_type=rel_type,
                confidence=confidence,
                evidence=item.get("evidence"),
            ))

        logger.info(f"Pass 3 跨课程关系: {len(relations)} 条 ({course_id} ↔ others)")
        return relations

    # ────────────────────────────────────────────────
    # 持久化
    # ────────────────────────────────────────────────

    def persist(
        self,
        concepts: list[ExtractedConcept],
        relations: list[ExtractedRelation],
    ) -> dict[str, int]:
        """
        将提取结果写入 SQLite。
        - 写入 concepts 表
        - 写入 chunk_concepts 关联表
        - 写入 concept_relations 表
        返回 {"concepts": N, "relations": M} 统计。
        """
        concept_count = 0
        for c in concepts:
            self._store.upsert_concept(
                concept_id=c.concept_id,
                name=c.name,
                course_id=c.course_id,
                definition=c.definition,
                concept_type=c.concept_type,
            )
            concept_count += 1
            # 建立 chunk ↔ concept 关联
            for cid in c.source_chunk_ids:
                self._store.link_chunk_to_concept(cid, c.concept_id)

        relation_count = 0
        for r in relations:
            # 确保两端概念都存在
            if self._store.get_concept(r.source_id) and self._store.get_concept(r.target_id):
                self._store.upsert_relation(
                    source_id=r.source_id,
                    target_id=r.target_id,
                    relation_type=r.relation_type,
                    confidence=r.confidence,
                    evidence=r.evidence,
                )
                relation_count += 1

        return {"concepts": concept_count, "relations": relation_count}

    # ────────────────────────────────────────────────
    # 辅助方法
    # ────────────────────────────────────────────────

    @staticmethod
    def _infer_type(chunk_type: str | None) -> str | None:
        """从 chunk 的 type 字段推断 concept_type。"""
        mapping = {
            "definition": "fundamental",
            "overview": "fundamental",
            "algorithm": "technique",
            "technique": "technique",
            "code": "technique",
            "example": "applied",
            "exercise": "applied",
            "theorem": "theorem",
        }
        return mapping.get(chunk_type or "")
