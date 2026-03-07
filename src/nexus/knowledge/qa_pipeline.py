"""
QA Pipeline — Nexus Phase 3 端到端问答入口

将 Phase 3 所有组件串联为一次完整的问答流程：

  用户问题
    ↓
  QueryEngine.expand_query()   → 改写为 2-3 个检索短语
    ↓
  QueryEngine.retrieve()       → 双路检索（语义 + 关键词）→ reranked top-5 chunks
    ↓
  context_assembler.assemble_messages()  → 组装 LLM messages
    ↓
  KnowledgeLLMClient.chat()    → LLM 生成回答
    ↓
  SQLiteStore.log_interaction() → 记录交互日志
    ↓
  QAResponse（回答 + 来源 + 元数据）

使用示例：
    from pathlib import Path
    from nexus.knowledge.qa_pipeline import QAPipeline

    pipeline = QAPipeline(
        chroma_dir=Path("data/chroma"),
        sqlite_path=Path("data/nexus.db"),
    )
    response = pipeline.ask(
        "什么是死锁的四个必要条件？",
        session_id="session_001",
        course_id="CS202_OS",
    )
    print(response.answer)
    for src in response.sources:
        print(f"  来源: {src}")
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context_assembler import assemble_messages, estimate_tokens
from .llm_client import KnowledgeLLMClient, LLMOutput
from .query_engine import QueryEngine, RetrievedChunk, RetrievalResult
from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────

@dataclass
class ChunkSource:
    """单个 chunk 的来源信息，用于回答中的引用说明。"""
    chunk_id: str
    topic: str
    source_file: str
    lecture_number: int | None
    page: int | None
    score: float

    def __str__(self) -> str:
        parts = [self.topic, f"(来自 {self.source_file}"]
        if self.lecture_number:
            parts[-1] += f", Lecture {self.lecture_number}"
        if self.page:
            parts[-1] += f", 第 {self.page} 页"
        parts[-1] += ")"
        return " ".join(parts)


@dataclass
class QAResponse:
    """问答结果。"""
    answer: str                             # LLM 生成的回答
    sources: list[ChunkSource]             # 使用的 chunk 来源列表
    session_id: str                         # 本次问答所属的 session
    course_id: str | None                   # 检索使用的课程
    model_used: str                         # 实际使用的 LLM 模型
    expanded_queries: list[str]            # Query Expansion 生成的检索短语
    estimated_input_tokens: int            # 估算的 prompt token 数
    warnings: list[str] = field(default_factory=list)

    def format_sources(self) -> str:
        """格式化来源列表，用于 UI 展示。"""
        if not self.sources:
            return "（未使用课件内容）"
        return "\n".join(f"  [{i+1}] {src}" for i, src in enumerate(self.sources))


# ────────────────────────────────────────────────
# Pipeline
# ────────────────────────────────────────────────

class QAPipeline:
    """
    端到端问答 Pipeline。

    参数：
    - chroma_dir: ChromaDB 持久化目录
    - sqlite_path: SQLite 数据库路径（存 profile + interaction_logs）
    - gemini_api_key: Gemini API key（Query Expansion + fallback 生成）
    - qa_model: 问答生成模型（litellm 格式，默认 gemini/gemini-2.0-flash）
    - top_k: 最终使用的 chunk 数量（默认 5）
    """

    def __init__(
        self,
        chroma_dir: Path,
        sqlite_path: Path,
        gemini_api_key: str | None = None,
        qa_model: str | None = None,
        top_k: int = 5,
    ) -> None:
        self._sqlite = SQLiteStore(sqlite_path)
        self._query_engine = QueryEngine(
            chroma_dir=chroma_dir,
            gemini_api_key=gemini_api_key,
            top_k=top_k,
        )
        self._llm = KnowledgeLLMClient(
            sqlite_store=self._sqlite,
            model=qa_model,
        )

    def ask(
        self,
        user_query: str,
        *,
        session_id: str | None = None,
        course_id: str | None = None,
    ) -> QAResponse:
        """
        执行完整问答流程，返回 QAResponse。

        参数：
        - user_query: 用户的问题（中英文均可）
        - session_id: 会话 ID（不传则自动生成；同一会话应保持一致以便 profile 更新）
        - course_id: 明确指定课程（如 "CS202_OS"），不传则全局检索
        """
        session_id = session_id or str(uuid.uuid4())[:8]
        warnings: list[str] = []

        # ── Step 1: 读取 user_profile（注入检索和 prompt） ──
        profile = self._sqlite.get_profile()
        profile_summary = _summarize_profile(profile)

        # ── Step 2: Dual Retrieval ──
        try:
            retrieval: RetrievalResult = self._query_engine.retrieve(
                user_query,
                course_id=course_id,
                profile_summary=profile_summary,
            )
        except Exception as exc:
            logger.error(f"检索失败: {exc}")
            warnings.append(f"检索失败: {exc}")
            retrieval = RetrievalResult(chunks=[], expanded_queries=[user_query], course_id=course_id)

        if not retrieval.chunks:
            warnings.append("未在知识库中找到相关课件内容，回答基于通用知识")

        # ── Step 3: 组装 messages ──
        messages = assemble_messages(
            user_query=user_query,
            chunks=retrieval.chunks,
            profile=profile,
            course_id=course_id,
        )
        estimated_tokens = estimate_tokens(messages)
        logger.info(f"Prompt 估算 tokens: {estimated_tokens}")

        # ── Step 4: LLM 生成回答 ──
        try:
            output: LLMOutput = self._llm.chat(messages)
        except Exception as exc:
            logger.error(f"LLM 生成失败: {exc}")
            return QAResponse(
                answer=f"抱歉，生成回答时出错：{exc}",
                sources=[],
                session_id=session_id,
                course_id=course_id,
                model_used="none",
                expanded_queries=retrieval.expanded_queries,
                estimated_input_tokens=estimated_tokens,
                warnings=warnings + [str(exc)],
            )

        # ── Step 5: 记录 interaction_logs ──
        chunk_ids = [c.chunk_id for c in retrieval.chunks]
        self._llm.log_interaction(
            session_id=session_id,
            user_query=user_query,
            llm_response=output.content,
            output=output,
            course_id=course_id,
            chunks_used=chunk_ids,
        )

        # ── 构建来源列表 ──
        sources = _build_sources(retrieval.chunks)

        logger.info(
            f"问答完成 | session={session_id} | chunks={len(retrieval.chunks)} | "
            f"model={output.model_used}"
        )

        return QAResponse(
            answer=output.content,
            sources=sources,
            session_id=session_id,
            course_id=course_id,
            model_used=output.model_used,
            expanded_queries=retrieval.expanded_queries,
            estimated_input_tokens=estimated_tokens,
            warnings=warnings,
        )

    def get_session_history(self, session_id: str) -> list[dict]:
        """获取 session 的历史问答记录（Week 3 profile update 时使用）。"""
        return self._sqlite.get_session_logs(session_id)


# ────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────

def _summarize_profile(profile: dict) -> str:
    """
    将 user_profile 摘要为几行文字，用于 Query Expansion prompt。
    只注入高价值字段，不全量序列化。
    """
    if not profile:
        return ""

    parts: list[str] = []
    courses = profile.get("courses") or []
    if courses:
        parts.append(f"课程：{', '.join(courses[:5])}")

    weak_points = profile.get("weak_points") or []
    if weak_points:
        concepts = [wp["concept"] for wp in weak_points[:3] if isinstance(wp, dict)]
        if concepts:
            parts.append(f"薄弱点：{', '.join(concepts)}")

    return "；".join(parts)


def _build_sources(chunks: list[RetrievedChunk]) -> list[ChunkSource]:
    """将 RetrievedChunk 列表转换为 ChunkSource 列表（用于展示）。"""
    sources: list[ChunkSource] = []
    for chunk in chunks:
        meta = chunk.metadata
        lecture_num = meta.get("lecture_number")
        page = meta.get("page")
        sources.append(ChunkSource(
            chunk_id=chunk.chunk_id,
            topic=str(meta.get("topic", "未知知识点")),
            source_file=str(meta.get("source_file", "未知文件")),
            lecture_number=int(lecture_num) if lecture_num and int(lecture_num) > 0 else None,
            page=int(page) if page and int(page) > 0 else None,
            score=chunk.score,
        ))
    return sources
