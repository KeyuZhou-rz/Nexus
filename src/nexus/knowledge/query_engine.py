"""
Query Engine — Nexus Phase 3

实现两条并行检索路径：
  路径 A（语义搜索）：对每个 expanded query 调用 ChromaDB 向量搜索
  路径 B（关键词过滤）：从用户问题提取技术关键词，用 ChromaDB where 精确过滤

两路结果合并去重后 rerank，输出 top-3 到 top-5 个最相关 chunks。

依赖：
  - google-generativeai（Query Expansion）
  - chromadb（向量检索）
  - nexus.knowledge.store（ChromaKnowledgeStore）
  - nexus.knowledge.sqlite_store（SQLiteStore，读取 user_profile）
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .prompts import QUERY_EXPAND_PROMPT
from .store import ChromaKnowledgeStore

logger = logging.getLogger(__name__)


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
# Query Expansion（LLM 改写）
# ────────────────────────────────────────────────

def _expand_query_with_gemini(
    user_query: str,
    api_key: str,
    course_context: str = "",
    profile_summary: str = "",
) -> list[str]:
    """
    用 Gemini Flash 将用户问题改写为 2-3 个检索短语。
    使用 JSON mode 确保输出格式正确。
    """
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)

    prompt = QUERY_EXPAND_PROMPT.format(
        user_query=user_query,
        course_context=course_context or "未指定课程",
        profile_summary=profile_summary or "无画像信息",
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
            max_output_tokens=256,
        ),
    )
    raw = response.text.strip()
    # 去掉可能的 markdown 代码块包装
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw)
    # 确保返回的是字符串列表
    if isinstance(result, list):
        return [str(q).strip() for q in result if str(q).strip()]
    return [user_query]  # fallback: 直接使用原始问题


def _expand_query_simple(user_query: str) -> list[str]:
    """
    无 LLM 时的轻量 fallback：提取技术关键词组合成检索短语。
    策略：原始问题 + 去停用词版本 + 核心实体短语
    """
    # 常见中英文停用词
    stopwords = {
        "什么", "是", "的", "了", "吗", "呢", "如何", "怎么", "怎样",
        "为什么", "请", "帮", "我", "explain", "what", "is", "how",
        "why", "can", "you", "please", "tell", "me", "about", "the", "a", "an",
    }
    words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", user_query)
    filtered = [w for w in words if w.lower() not in stopwords and len(w) > 1]

    queries = [user_query]
    if filtered:
        # 去停用词后的短语作为第二个查询
        queries.append(" ".join(filtered))
    return queries[:3]


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
        "请", "帮", "我", "要", "能", "会", "关于", "介绍",
        "explain", "what", "is", "how", "why", "can", "please", "about",
        "tell", "me", "the", "a", "an", "and", "or", "in", "on", "at", "to", "for",
    }
    words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", user_query)
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
) -> list[RetrievedChunk]:
    """
    合并两路检索结果，去重后重排序，返回 top_k 个 chunks。

    Reranking 策略：
    - 同时出现在两路结果中的 chunk：取较低（更好）的距离分，再额外 -0.05 奖励
    - 只在语义路径中的 chunk：保持原始距离
    - 只在关键词路径中的 chunk：保持原始距离，+0.05 轻微惩罚（精确匹配但语义可能稍偏）
    - 最终按距离升序排列，取 top_k
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

    # 按 score 升序排列（score 越小越相关）
    ranked = sorted(merged.values(), key=lambda x: x["score"])[:top_k]

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
    - gemini_api_key: 用于 Query Expansion（不传则从环境变量读取）
    - n_semantic: 语义路径每个 expanded query 返回的候选数（默认 5）
    - n_keyword: 关键词路径每次过滤返回的候选数（默认 5）
    - top_k: 最终 rerank 后保留的 chunk 数（默认 5）
    """

    def __init__(
        self,
        chroma_dir: Path,
        gemini_api_key: str | None = None,
        n_semantic: int = 5,
        n_keyword: int = 5,
        top_k: int = 5,
    ) -> None:
        self._chroma = ChromaKnowledgeStore(chroma_dir, per_course=True)
        self._api_key = (
            gemini_api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
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
        有 Gemini API Key 时调用 LLM，否则用轻量规则 fallback。
        """
        course_context = course_id or "未指定课程"

        if self._api_key:
            try:
                expanded = _expand_query_with_gemini(
                    user_query,
                    self._api_key,
                    course_context=course_context,
                    profile_summary=profile_summary,
                )
                logger.debug(f"Query expansion: {user_query!r} → {expanded}")
                return expanded
            except Exception as exc:
                logger.warning(f"Query expansion 失败，使用 fallback: {exc}")

        return _expand_query_simple(user_query)

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
        - course_id: 限定检索范围（None 则尝试跨所有课程，暂时 fallback 到默认 collection）
        - expanded_queries: 预先扩展好的查询（不传则内部自动 expand）
        """
        # ── Query Expansion ──
        if expanded_queries is None:
            expanded_queries = self.expand_query(user_query, course_id, profile_summary)

        # ── 路径 A：语义搜索 ──
        semantic_raw: list[tuple[str, str, dict, float]] = []
        seen_semantic: set[str] = set()

        for query in expanded_queries:
            try:
                result = self._chroma.query(
                    query,
                    n_results=self.n_semantic,
                    course_id=course_id,
                )
                docs = (result.get("documents") or [[]])[0]
                metas = (result.get("metadatas") or [[]])[0]
                ids = (result.get("ids") or [[]])[0]
                dists = (result.get("distances") or [[]])[0]

                for cid, doc, meta, dist in zip(ids, docs, metas, dists):
                    if cid not in seen_semantic:
                        seen_semantic.add(cid)
                        semantic_raw.append((cid, doc, meta or {}, dist))
            except Exception as exc:
                logger.warning(f"语义检索失败（query={query!r}）: {exc}")

        # ── 路径 B：关键词过滤搜索 ──
        keyword_raw: list[tuple[str, str, dict, float]] = []
        keywords = _extract_keywords(user_query)

        if keywords:
            # 对每个关键词单独做 where 过滤（$or 在某些 ChromaDB 版本不稳定）
            seen_keyword: set[str] = set()
            for kw in keywords[:3]:  # 最多取前 3 个关键词，避免请求过多
                try:
                    # $contains：检查 keywords 字符串中是否包含该子串
                    result = self._chroma.query(
                        user_query,                         # 仍用原始问题做向量检索
                        n_results=self.n_keyword,
                        where={"keywords": {"$contains": kw}},
                        course_id=course_id,
                    )
                    docs = (result.get("documents") or [[]])[0]
                    metas = (result.get("metadatas") or [[]])[0]
                    ids = (result.get("ids") or [[]])[0]
                    dists = (result.get("distances") or [[]])[0]

                    for cid, doc, meta, dist in zip(ids, docs, metas, dists):
                        if cid not in seen_keyword:
                            seen_keyword.add(cid)
                            keyword_raw.append((cid, doc, meta or {}, dist))
                except Exception as exc:
                    # 关键词路径失败时静默降级（语义路径仍可用）
                    logger.debug(f"关键词检索跳过（kw={kw!r}）: {exc}")

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
