"""
Query Engine — Nexus Phase N3

实现两条并行检索路径：
  路径 A（语义搜索）：对每个 expanded query 调用 ChromaDB 向量搜索
  路径 B（关键词过滤）：从用户问题提取技术关键词，用 ChromaDB where 精确过滤

两路结果合并去重后 rerank，输出 top-3 到 top-5 个最相关 chunks。

查询扩展（N3 优化）：
  默认走规则扩展（关键词提取 + 概念别名展开），零 LLM 成本。
  use_llm_expansion=True 时附加 LLM 语义改写（Pro 用户）。

依赖：
  - chromadb（向量检索）
  - nexus.knowledge.store（ChromaKnowledgeStore）
  - nexus.knowledge.sqlite_store（SQLiteStore）
  - nexus.knowledge.concept_resolver（ConceptResolver，别名展开）
  - nexus.knowledge.llm_gateway（可选 LLM 查询扩展）
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .prompts import QUERY_EXPAND_PROMPT
from .store import ChromaKnowledgeStore, CourseCollectionNotFoundError
from .llm_gateway import gateway

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """Raised when the semantic retrieval backend fails for every query."""


# ────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────

@dataclass
class RetrievedChunk:
    """单个检索到的 chunk，附带来源信息和相关性分数。"""
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    score: float                # 距离分数（越小越相关，余弦距离）
    retrieval_path: str         # "semantic" | "keyword" | "both"


@dataclass
class RetrievalResult:
    """Dual Retrieval 的最终输出。"""
    chunks: list[RetrievedChunk]        # 已去重 rerank 的 top chunks
    expanded_queries: list[str]         # Query Expansion 生成的检索短语
    course_id: str | None               # 检索限定的课程（None = 全局搜索）


# ────────────────────────────────────────────────
# Query Expansion（规则主路径 + LLM 可选增强）
# ────────────────────────────────────────────────


def _expand_query_rule(
    user_query: str,
    course_id: str | None = None,
) -> list[str]:
    """
    基于规则生成 2-3 个检索短语（主路径，零 LLM 成本）。

    策略：
    1. 原始问题（保留完整语义）
    2. 技术关键词提取（去停用词后的核心术语）
    3. 概念别名展开（利用 concept_resolver 三级匹配）

    返回去重后的查询列表，最多 3 个。
    """
    queries: list[str] = [user_query.strip()]

    # ── 技术关键词提取 ──
    keywords = _extract_keywords(user_query)
    if keywords:
        queries.append(" ".join(keywords))

    # ── 概念别名展开 ──
    if course_id and keywords:
        try:
            from .sqlite_store import SQLiteStore, default_db_path
            from .concept_resolver import ConceptResolver
            store = SQLiteStore(default_db_path())
            resolver = ConceptResolver(store)
            expanded_terms: list[str] = []
            for kw in keywords[:3]:
                result = resolver.resolve(kw, course_id)
                if result and result.level <= 2:
                    expanded_terms.append(result.name)
            if expanded_terms:
                queries.append(" ".join(expanded_terms))
        except Exception:
            pass

    # 去重 + 截断
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        normalized = q.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(q.strip())
    return unique[:3]


def _expand_query_with_llm(
    user_query: str,
    course_context: str = "",
    profile_summary: str = "",
) -> list[str]:
    """
    可选 LLM 增强：用 gateway.chat() 将用户问题改写为 2-3 个检索短语。
    仅在 Pro 用户且 gateway.is_configured 时调用。
    """
    prompt = QUERY_EXPAND_PROMPT.format(
        user_query=user_query,
        course_context=course_context or "未指定课程",
        profile_summary=profile_summary or "无画像信息",
    )

    resp = gateway.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=256,
        response_format={"type": "json_object"},
    )
    raw = resp.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw.strip())
    if isinstance(result, list):
        return [str(q).strip() for q in result if str(q).strip()]
    for key in ("queries", "items", "results"):
        if key in result and isinstance(result[key], list):
            return [str(q).strip() for q in result[key] if str(q).strip()]
    return [user_query]


# ────────────────────────────────────────────────
# 关键词提取（路径 B 使用）
# ────────────────────────────────────────────────

def _extract_keywords(user_query: str) -> list[str]:
    """
    从用户问题中提取可能在 chunk metadata keywords 里出现的技术关键词。
    策略：保留长度 >= 2 的词，过滤常见停用词，最多取 5 个。
    注意：keywords 在 ChromaDB 中以逗号分隔字符串存储，$contains 做子串匹配。
    """
    stopwords = {
        "什么", "是", "的", "了", "吗", "呢", "如何", "怎么", "怎样", "为什么",
        "请", "帮", "我", "要", "能", "会", "关于", "介绍", "解释", "和", "有", "区别",
        "explain", "what", "is", "how", "why", "can", "please", "about",
        "tell", "me", "the", "a", "an", "and", "or", "in", "on", "at", "to", "for",
    }
    prepared = user_query
    chinese_stopwords = sorted(
        (word for word in stopwords if re.search(r"[一-鿿]", word)),
        key=len,
        reverse=True,
    )
    for stopword in chinese_stopwords:
        prepared = prepared.replace(stopword, " ")

    words = re.findall(r"[a-zA-Z0-9]+|[一-鿿]{2,}", prepared)
    keywords = [
        w for w in words
        if w.lower() not in stopwords and len(w) >= 2
    ]
    # 去重保序
    seen: set[str] = set()
    unique = []
    for kw in keywords:
        if kw.lower() not in seen:
            seen.add(kw.lower())
            unique.append(kw)
    return unique[:5]


# ────────────────────────────────────────────────
# 合并去重 + Rerank
# ────────────────────────────────────────────────

def _merge_and_rerank(
    semantic_results: list[tuple[str, str, dict, float]],   # (id, content, meta, distance)
    keyword_results: list[tuple[str, str, dict, float]],
    top_k: int = 5,
    max_distance: float = 0.55,   # 余弦距离截断阈值；> 此值视为不相关，不纳入结果
) -> list[RetrievedChunk]:
    """
    合并两路检索结果，去重后重排序，返回 top_k 个 chunks。

    Reranking 策略：
    - 同时出现在两路结果中的 chunk：取较低（更好）的距离分，再额外 -0.05 奖励
    - 只在语义路径中的 chunk：保持原始距离
    - 只在关键词路径中的 chunk：保持原始距离，+0.05 轻微惩罚（精确匹配但语义可能稍偏）
    - 最终按距离升序排列，取 top_k
    - 最终结果中过滤掉 score > max_distance 的 chunk（不相关）
    """
    # 用 chunk_id 作为 key 合并
    merged: dict[str, dict] = {}

    for chunk_id, content, meta, dist in semantic_results:
        merged[chunk_id] = {
            "chunk_id": chunk_id,
            "content": content,
            "metadata": meta,
            "score": dist,
            "paths": {"semantic"},
        }

    for chunk_id, content, meta, dist in keyword_results:
        if chunk_id in merged:
            # 已在语义结果中：取更好的分数 + 奖励
            merged[chunk_id]["score"] = min(merged[chunk_id]["score"], dist) - 0.05
            merged[chunk_id]["paths"].add("keyword")
        else:
            # 仅在关键词结果中：轻微惩罚
            merged[chunk_id] = {
                "chunk_id": chunk_id,
                "content": content,
                "metadata": meta,
                "score": dist + 0.05,
                "paths": {"keyword"},
            }

    # 按 score 升序排列（score 越小越相关），截断 max_distance
    ranked = [
        r for r in sorted(merged.values(), key=lambda x: x["score"])
        if r["score"] <= max_distance
    ][:top_k]

    return [
        RetrievedChunk(
            chunk_id=r["chunk_id"],
            content=r["content"],
            metadata=r["metadata"],
            score=r["score"],
            retrieval_path=(
                "both" if len(r["paths"]) > 1
                else next(iter(r["paths"]))
            ),
        )
        for r in ranked
    ]


# ────────────────────────────────────────────────
# 主类
# ────────────────────────────────────────────────

class QueryEngine:
    """
    Dual Retrieval Query Engine。

    参数：
    - chroma_dir: ChromaDB 持久化目录
    - use_llm_expansion: 是否启用 LLM 查询增强（默认 False，走规则路径）
    - n_semantic: 语义路径每个 expanded query 返回的候选数（默认 5）
    - n_keyword: 关键词路径每次过滤返回的候选数（默认 5）
    - top_k: 最终 rerank 后保留的 chunk 数（默认 5）
    """

    def __init__(
        self,
        chroma_dir: Path,
        use_llm_expansion: bool = False,
        n_semantic: int = 5,
        n_keyword: int = 5,
        top_k: int = 5,
    ) -> None:
        self._chroma = ChromaKnowledgeStore(chroma_dir, per_course=True)
        self.use_llm_expansion = use_llm_expansion
        self.n_semantic = n_semantic
        self.n_keyword = n_keyword
        self.top_k = top_k

    # ────────────────────────────────────────────────
    # Query Expansion
    # ────────────────────────────────────────────────

    def expand_query(
        self,
        user_query: str,
        course_id: str | None = None,
        profile_summary: str = "",
    ) -> list[str]:
        """
        将用户问题扩展为 2-3 个检索短语。
        默认走规则路径（零 LLM 成本）；use_llm_expansion=True 时附加 LLM 增强。
        """
        # ── 主路径：规则扩展（始终执行）──
        expanded = _expand_query_rule(user_query, course_id)

        # ── 可选增强：LLM 改写 ──
        if self.use_llm_expansion and gateway.is_configured:
            try:
                course_context = course_id or "未指定课程"
                llm_queries = _expand_query_with_llm(
                    user_query,
                    course_context=course_context,
                    profile_summary=profile_summary,
                )
                # 合并规则结果和 LLM 结果（LLM 在前，语义质量更高）
                combined = llm_queries + expanded
                seen: set[str] = set()
                merged: list[str] = []
                for q in combined:
                    n = q.strip().lower()
                    if n and n not in seen:
                        seen.add(n)
                        merged.append(q.strip())
                expanded = merged[:3]
                logger.debug(f"Query expansion (rule+LLM): {user_query!r} -> {expanded}")
            except Exception as exc:
                logger.warning(f"LLM query expansion failed, using rule-only: {exc}")

        return expanded

    # ────────────────────────────────────────────────
    # Dual Retrieval
    # ────────────────────────────────────────────────

    def retrieve(
        self,
        user_query: str,
        course_id: str | None = None,
        profile_summary: str = "",
        expanded_queries: list[str] | None = None,
    ) -> RetrievalResult:
        """
        执行双路检索并返回 RetrievalResult。

        路径 A（语义）：
          对每个 expanded query 调用 ChromaDB.query()，合并候选集
        路径 B（关键词）：
          提取技术关键词，用 ChromaDB where filter 精确过滤

        参数：
        - course_id: 限定检索范围（None 时跨所有已有课程 collection）
        - expanded_queries: 预先扩展好的查询（不传则内部自动 expand）
        """
        # ── Query Expansion ──
        if expanded_queries is None:
            expanded_queries = self.expand_query(user_query, course_id, profile_summary)

        # ── 路径 A：语义搜索 ──
        semantic_raw: list[tuple[str, str, dict, float]] = []
        seen_semantic: set[str] = set()
        semantic_successes = 0
        semantic_errors: list[Exception] = []

        for query in expanded_queries:
            try:
                result = self._chroma.query(
                    query,
                    n_results=self.n_semantic,
                    course_id=course_id,
                )
                semantic_successes += 1
                docs = (result.get("documents") or [[]])[0]
                metas = (result.get("metadatas") or [[]])[0]
                ids = (result.get("ids") or [[]])[0]
                dists = (result.get("distances") or [[]])[0]

                for cid, doc, meta, dist in zip(ids, docs, metas, dists):
                    if cid not in seen_semantic:
                        seen_semantic.add(cid)
                        semantic_raw.append((cid, doc, meta or {}, dist))
            except CourseCollectionNotFoundError:
                raise
            except Exception as exc:
                semantic_errors.append(exc)
                logger.warning(f"语义检索失败（query={query!r}）: {exc}")

        if expanded_queries and semantic_successes == 0:
            detail = str(semantic_errors[-1]) if semantic_errors else "unknown error"
            raise RetrievalError(f"Semantic retrieval failed for all queries: {detail}")

        # ── 路径 B：关键词过滤搜索 ──
        keyword_raw: list[tuple[str, str, dict, float]] = []
        keywords = _extract_keywords(user_query)

        if keywords:
            try:
                result = self._chroma.query_keywords(
                    keywords[:5],
                    n_results=self.n_keyword,
                    course_id=course_id,
                )
                docs = (result.get("documents") or [[]])[0]
                metas = (result.get("metadatas") or [[]])[0]
                ids = (result.get("ids") or [[]])[0]
                dists = (result.get("distances") or [[]])[0]
                keyword_raw.extend(
                    (cid, doc, meta or {}, dist)
                    for cid, doc, meta, dist in zip(ids, docs, metas, dists)
                )
            except Exception as exc:
                # 关键词路径失败时静默降级（语义路径仍可用）
                logger.debug(f"关键词检索跳过: {exc}")

        # ── 合并去重 + Rerank ──
        chunks = _merge_and_rerank(semantic_raw, keyword_raw, top_k=self.top_k)

        logger.info(
            f"检索完成: {len(chunks)} chunks | "
            f"语义候选 {len(semantic_raw)} + 关键词候选 {len(keyword_raw)} | "
            f"expanded={expanded_queries}"
        )

        return RetrievalResult(
            chunks=chunks,
            expanded_queries=expanded_queries,
            course_id=course_id,
        )
