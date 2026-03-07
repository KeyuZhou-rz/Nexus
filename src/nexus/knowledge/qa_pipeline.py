"""
QA Pipeline — Nexus Phase 3 端到端问答入口（含 Week 3 扩展）

完整流程：
  用户问题
    ↓  [Week 3 新增] 课程上下文推断（5 级优先级）
    ↓
  QueryEngine.expand_query()         → 改写为 2-3 个检索短语
    ↓
  QueryEngine.retrieve()             → 双路检索 → reranked top-5
    ↓
  context_assembler.assemble_messages()  → 组装 messages（含 profile 注入）
    ↓
  KnowledgeLLMClient.chat()          → LLM 生成回答
    ↓
  SQLiteStore.log_interaction()      → 记录交互日志
    ↓  [Week 3 新增] SessionManager.record_activity()
    ↓
  QAResponse

课程推断优先级（从高到低）：
  1. 用户显式指定 course_id 参数
  2. 用户问题中包含课程关键词（CS202、OS、Database 等）
  3. 本 session 中之前问答使用的课程
  4. Calendar：最近 48h 内有 due 的任务对应课程
  5. interaction_logs：最近对话最多的课程
  6. None（全局搜索，不限定课程）
"""
from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .context_assembler import assemble_messages, estimate_tokens
from .llm_client import KnowledgeLLMClient, LLMOutput
from .query_engine import QueryEngine, RetrievedChunk, RetrievalResult
from .session_manager import SessionManager
from .sqlite_store import SQLiteStore
from .user_profile import UserProfileManager

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
    course_inferred: bool                   # 课程是否为推断结果（而非显式指定）
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
# 课程上下文推断
# ────────────────────────────────────────────────

# 常见课程关键词到 course_id 前缀的映射（用户可扩展）
# 这里是启发式规则，实际 course_id 需要与 ChromaDB collection 名称匹配
_COURSE_KEYWORDS: dict[str, str] = {
    # CS 课程
    "operating system": "OS", "os": "OS", "操作系统": "OS",
    "database": "DB", "数据库": "DB", "sql": "DB",
    "algorithm": "ALGO", "algorithms": "ALGO", "算法": "ALGO",
    "data structure": "DS", "数据结构": "DS",
    "computer network": "NET", "network": "NET", "计算机网络": "NET",
    "machine learning": "ML", "ml": "ML", "机器学习": "ML",
    "deep learning": "DL", "dl": "DL", "深度学习": "DL",
    "computer architecture": "ARCH", "arch": "ARCH", "计算机体系结构": "ARCH",
    # 电气/物理
    "circuits": "CIRCUIT", "电路": "CIRCUIT",
    "signals": "SIGNAL", "信号": "SIGNAL",
}


def _infer_course_from_query(user_query: str, known_courses: list[str]) -> str | None:
    """
    从用户问题文本中提取课程关键词。
    策略 1：直接匹配 known_courses 中的 course_id（精确匹配优先）
    策略 2：匹配 _COURSE_KEYWORDS 中的关键词→前缀，再在 known_courses 中找最佳匹配
    """
    query_lower = user_query.lower()

    # 策略 1：用户问题直接包含某个已知 course_id
    for course_id in known_courses:
        if course_id.lower() in query_lower:
            return course_id

    # 策略 2：关键词映射
    for keyword, prefix in _COURSE_KEYWORDS.items():
        if keyword in query_lower:
            # 在 known_courses 中找以该前缀开头或包含该前缀的 course_id
            for course_id in known_courses:
                if prefix.lower() in course_id.lower():
                    return course_id
            # 即使没有精确匹配，返回前缀让调用方决定是否用
            return prefix

    return None


def _infer_course_from_session(
    session_id: str,
    sqlite_path: Path,
) -> str | None:
    """
    从当前 session 的历史 interaction_logs 中推断课程。
    取本 session 中最频繁使用的非空 course_id。
    """
    try:
        conn = sqlite3.connect(str(sqlite_path))
        rows = conn.execute(
            """
            SELECT course_id, COUNT(*) as cnt
            FROM interaction_logs
            WHERE session_id = ? AND course_id IS NOT NULL AND course_id != ''
            GROUP BY course_id
            ORDER BY cnt DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchall()
        conn.close()
        if rows:
            return rows[0][0]
    except Exception as exc:
        logger.debug(f"session 课程推断失败: {exc}")
    return None


def _infer_course_from_calendar(tasks_path: Path) -> str | None:
    """
    从 tasks.json 中找最近 48 小时内有 due 的任务对应课程。
    对应 spec 的 "Calendar 关联" 推断策略。
    """
    if not tasks_path.exists():
        return None
    try:
        import json
        tasks_raw = json.loads(tasks_path.read_text(encoding="utf-8"))
        tasks = tasks_raw if isinstance(tasks_raw, list) else tasks_raw.get("tasks", [])
        now = datetime.now().astimezone()
        window_end = now + timedelta(hours=48)

        for task in tasks:
            due_at_raw = task.get("due_at")
            course = task.get("course")
            if not due_at_raw or not course:
                continue
            # 过滤明显不是课程代码的值（邮件地址等）
            if "@" in course or len(course) > 30:
                continue
            try:
                due_at = datetime.fromisoformat(due_at_raw)
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=now.tzinfo)
                if now <= due_at <= window_end:
                    return course
            except (ValueError, TypeError):
                continue
    except Exception as exc:
        logger.debug(f"Calendar 课程推断失败: {exc}")
    return None


def _infer_course_from_history(sqlite_path: Path) -> str | None:
    """
    从全局 interaction_logs 中找最近 7 天内使用最多的课程。
    对应 spec 的 "最近活跃" 推断策略。
    """
    try:
        conn = sqlite3.connect(str(sqlite_path))
        rows = conn.execute(
            """
            SELECT course_id, COUNT(*) as cnt
            FROM interaction_logs
            WHERE course_id IS NOT NULL AND course_id != ''
              AND timestamp >= datetime('now', '-7 days')
            GROUP BY course_id
            ORDER BY cnt DESC
            LIMIT 1
            """,
        ).fetchall()
        conn.close()
        if rows:
            return rows[0][0]
    except Exception as exc:
        logger.debug(f"历史活跃课程推断失败: {exc}")
    return None


def infer_course_id(
    user_query: str,
    session_id: str,
    sqlite_path: Path,
    known_courses: list[str],
    tasks_path: Path | None = None,
) -> tuple[str | None, str]:
    """
    按 5 级优先级推断课程上下文，返回 (course_id, inference_source)。

    优先级（从高到低）：
      1. 问题文本中的显式课程关键词
      2. 本 session 历史 interaction_logs 中的课程
      3. Calendar：最近 48h 有 due 的任务课程
      4. 全局历史：最近 7 天最活跃课程
      5. None（全局搜索）
    """
    # Level 1: 问题文本
    course = _infer_course_from_query(user_query, known_courses)
    if course:
        return course, "query_text"

    # Level 2: Session 历史
    course = _infer_course_from_session(session_id, sqlite_path)
    if course:
        return course, "session_history"

    # Level 3: Calendar
    if tasks_path:
        course = _infer_course_from_calendar(tasks_path)
        if course:
            return course, "calendar"

    # Level 4: 全局历史活跃
    course = _infer_course_from_history(sqlite_path)
    if course:
        return course, "activity_history"

    # Level 5: 全局搜索
    return None, "global"


# ────────────────────────────────────────────────
# Pipeline
# ────────────────────────────────────────────────

class QAPipeline:
    """
    端到端问答 Pipeline（Phase 3 完整版）。

    参数：
    - chroma_dir: ChromaDB 持久化目录
    - sqlite_path: SQLite 数据库路径
    - tasks_path: tasks.json 路径（用于 Calendar 课程推断，可选）
    - gemini_api_key: Gemini API key
    - qa_model: 问答生成模型（litellm 格式）
    - top_k: 最终使用的 chunk 数量
    """

    def __init__(
        self,
        chroma_dir: Path,
        sqlite_path: Path,
        tasks_path: Path | None = None,
        gemini_api_key: str | None = None,
        qa_model: str | None = None,
        top_k: int = 5,
    ) -> None:
        self._sqlite = SQLiteStore(sqlite_path)
        self._sqlite_path = sqlite_path
        self._tasks_path = tasks_path

        self._profile_mgr = UserProfileManager(self._sqlite)
        self._session_mgr = SessionManager(
            sqlite_store=self._sqlite,
            profile_manager=self._profile_mgr,
            gemini_api_key=gemini_api_key,
        )
        self._query_engine = QueryEngine(
            chroma_dir=chroma_dir,
            gemini_api_key=gemini_api_key,
            top_k=top_k,
        )
        self._llm = KnowledgeLLMClient(
            sqlite_store=self._sqlite,
            model=qa_model,
        )

    # ────────────────────────────────────────────────
    # 主问答入口
    # ────────────────────────────────────────────────

    def ask(
        self,
        user_query: str,
        *,
        session_id: str | None = None,
        course_id: str | None = None,
    ) -> QAResponse:
        """
        执行完整问答流程。

        参数：
        - user_query: 用户问题（中英文均可）
        - session_id: 会话 ID（不传则自动生成）
        - course_id: 显式指定课程；不传则按 5 级优先级推断
        """
        session_id = session_id or str(uuid.uuid4())[:8]
        warnings: list[str] = []
        course_inferred = False

        # ── 启动/更新 session ──
        self._session_mgr.start_session(session_id, course_id)

        # ── Step 1: 读取 profile ──
        profile = self._profile_mgr.get()
        known_courses = profile.get("courses") or []

        # ── Step 2: 课程推断（仅在未显式指定时） ──
        if course_id is None:
            course_id, inference_source = infer_course_id(
                user_query=user_query,
                session_id=session_id,
                sqlite_path=self._sqlite_path,
                known_courses=known_courses,
                tasks_path=self._tasks_path,
            )
            if course_id:
                course_inferred = True
                logger.info(f"课程推断: {course_id!r} (来源: {inference_source})")
        else:
            inference_source = "explicit"

        # ── Step 3: Dual Retrieval ──
        profile_summary = _summarize_profile(profile)
        try:
            retrieval: RetrievalResult = self._query_engine.retrieve(
                user_query,
                course_id=course_id,
                profile_summary=profile_summary,
            )
        except Exception as exc:
            logger.error(f"检索失败: {exc}")
            warnings.append(f"检索失败: {exc}")
            retrieval = RetrievalResult(
                chunks=[], expanded_queries=[user_query], course_id=course_id
            )

        if not retrieval.chunks:
            warnings.append("未在知识库中找到相关课件内容，回答基于通用知识")

        # ── Step 4: 组装 messages ──
        messages = assemble_messages(
            user_query=user_query,
            chunks=retrieval.chunks,
            profile=profile,
            course_id=course_id,
        )
        estimated_tokens = estimate_tokens(messages)
        logger.info(f"Prompt 估算 tokens: {estimated_tokens}")

        # ── Step 5: LLM 生成 ──
        try:
            output: LLMOutput = self._llm.chat(messages)
        except Exception as exc:
            logger.error(f"LLM 生成失败: {exc}")
            return QAResponse(
                answer=f"抱歉，生成回答时出错：{exc}",
                sources=[],
                session_id=session_id,
                course_id=course_id,
                course_inferred=course_inferred,
                model_used="none",
                expanded_queries=retrieval.expanded_queries,
                estimated_input_tokens=estimated_tokens,
                warnings=warnings + [str(exc)],
            )

        # ── Step 6: 记录日志 + 更新 session 活动 ──
        chunk_ids = [c.chunk_id for c in retrieval.chunks]
        self._llm.log_interaction(
            session_id=session_id,
            user_query=user_query,
            llm_response=output.content,
            output=output,
            course_id=course_id,
            chunks_used=chunk_ids,
        )
        self._session_mgr.record_activity(session_id, course_id)

        sources = _build_sources(retrieval.chunks)
        logger.info(
            f"问答完成 | session={session_id} | course={course_id} ({inference_source}) | "
            f"chunks={len(retrieval.chunks)} | model={output.model_used}"
        )

        return QAResponse(
            answer=output.content,
            sources=sources,
            session_id=session_id,
            course_id=course_id,
            course_inferred=course_inferred,
            model_used=output.model_used,
            expanded_queries=retrieval.expanded_queries,
            estimated_input_tokens=estimated_tokens,
            warnings=warnings,
        )

    # ────────────────────────────────────────────────
    # Session 管理接口
    # ────────────────────────────────────────────────

    def end_session(self, session_id: str) -> dict[str, Any]:
        """
        显式结束 session，触发 LLM profile 更新分析。
        通常在用户关闭对话或点击"结束学习"时调用。
        """
        return self._session_mgr.end_session(session_id)

    def check_idle_sessions(self) -> list[str]:
        """
        检查并结束所有 idle 超时的 session。
        建议每 2-3 分钟由定时任务调用一次。
        """
        return self._session_mgr.check_and_end_idle_sessions()

    # ────────────────────────────────────────────────
    # Profile 管理接口（透传，方便调用方使用）
    # ────────────────────────────────────────────────

    def get_profile(self) -> dict[str, Any]:
        """获取当前 user profile。"""
        return self._profile_mgr.get()

    def add_course(self, course_id: str) -> None:
        """手动添加课程到 profile。"""
        self._profile_mgr.add_course(course_id)

    def get_session_history(self, session_id: str) -> list[dict]:
        """获取 session 的历史问答记录。"""
        return self._sqlite.get_session_logs(session_id)


# ────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────

def _summarize_profile(profile: dict) -> str:
    """
    将 profile 浓缩为几行摘要，注入 Query Expansion prompt。
    只包含高价值字段，不全量序列化。
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
    """将 RetrievedChunk 列表转换为 ChunkSource（UI 展示用）。"""
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
