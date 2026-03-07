"""
SQLite 存储层 — Nexus Phase 3

负责管理 documents / chunks / user_profile / interaction_logs / sync_metadata 五张表。
每次连接时强制开启 WAL 模式（并发安全）和外键约束。
所有写入操作均为幂等设计（INSERT OR REPLACE / INSERT OR IGNORE）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


# schema.sql 与本文件同目录
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _connect(db_path: Path) -> sqlite3.Connection:
    """
    创建 SQLite 连接并配置必要的 PRAGMA。
    - WAL 模式：允许并发读写，防止写入中断导致数据损坏
    - foreign_keys=ON：确保 CASCADE DELETE 生效
    每次连接都必须设置，SQLite PRAGMA 不持久化到文件。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row          # 让结果可按列名访问
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class SQLiteStore:
    """
    Nexus 结构化元数据存储。
    db_path: SQLite 文件路径，通常为 data/nexus.db
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """执行 schema.sql 建表（IF NOT EXISTS，幂等）。"""
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with _connect(self.db_path) as conn:
            conn.executescript(sql)

    # ────────────────────────────────────────────────
    # documents 表操作
    # ────────────────────────────────────────────────

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """按 id 查询文档记录，不存在则返回 None。"""
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_document(
        self,
        doc_id: str,
        course_id: str,
        filename: str,
        file_hash: str,
        parse_status: str = "pending",
        chunk_count: int = 0,
        parse_model: str | None = None,
    ) -> None:
        """
        插入或替换文档记录（幂等）。
        file_hash 变化时会覆盖旧记录，配合 CASCADE DELETE 清理关联 chunks。
        """
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                    (id, course_id, filename, file_hash, parse_status, chunk_count, parse_model)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, course_id, filename, file_hash, parse_status, chunk_count, parse_model),
            )

    def set_document_status(
        self,
        doc_id: str,
        status: str,
        chunk_count: int | None = None,
    ) -> None:
        """更新文档解析状态，可选同步 chunk_count。"""
        with _connect(self.db_path) as conn:
            if chunk_count is not None:
                conn.execute(
                    "UPDATE documents SET parse_status=?, chunk_count=? WHERE id=?",
                    (status, chunk_count, doc_id),
                )
            else:
                conn.execute(
                    "UPDATE documents SET parse_status=? WHERE id=?",
                    (status, doc_id),
                )

    def delete_document(self, doc_id: str) -> None:
        """
        删除文档记录，关联 chunks 因 CASCADE 自动删除。
        文件更新后重新 ingest 时调用。
        """
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    # ────────────────────────────────────────────────
    # chunks 表操作
    # ────────────────────────────────────────────────

    def upsert_chunk(
        self,
        chunk_id: str,
        document_id: str,
        course_id: str,
        topic: str,
        chunk_type: str | None = None,
        keywords: list[str] | None = None,
        prerequisites: list[str] | None = None,
        page: int | None = None,
        lecture_number: int | None = None,
    ) -> None:
        """
        插入或替换 chunk 元数据。
        keywords / prerequisites 以 JSON 数组字符串存储。
        chunk_id 与 ChromaDB 中对应 document 的 id 保持一致，方便交叉查询。
        """
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO chunks
                    (chunk_id, document_id, course_id, topic, type,
                     keywords, prerequisites, page, lecture_number)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    document_id,
                    course_id,
                    topic,
                    chunk_type,
                    json.dumps(keywords or [], ensure_ascii=False),
                    json.dumps(prerequisites or [], ensure_ascii=False),
                    page,
                    lecture_number,
                ),
            )

    def delete_chunks_by_document(self, document_id: str) -> None:
        """删除指定文档的所有 chunks（文件更新时先调用此方法再重新写入）。"""
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))

    # ────────────────────────────────────────────────
    # user_profile 表操作
    # ────────────────────────────────────────────────

    def get_profile(self, key: str = "profile") -> dict[str, Any]:
        """读取用户画像，不存在时返回空字典。"""
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM user_profile WHERE key=?", (key,)
            ).fetchone()
            if not row:
                return {}
            try:
                return json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                return {}

    def set_profile(self, data: dict[str, Any], key: str = "profile") -> None:
        """覆盖写入用户画像（整体替换）。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_profile (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (key, json.dumps(data, ensure_ascii=False)),
            )

    # ────────────────────────────────────────────────
    # interaction_logs 表操作
    # ────────────────────────────────────────────────

    def log_interaction(
        self,
        session_id: str,
        user_query: str,
        llm_response: str,
        *,
        course_id: str | None = None,
        chunks_used: list[str] | None = None,
        model_used: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """记录一次问答交互，用于后续 session 分析和画像更新。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO interaction_logs
                    (session_id, course_id, user_query, chunks_used,
                     llm_response, model_used, input_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    course_id,
                    user_query,
                    json.dumps(chunks_used or [], ensure_ascii=False),
                    llm_response,
                    model_used,
                    input_tokens,
                    output_tokens,
                ),
            )

    def get_session_logs(self, session_id: str) -> list[dict[str, Any]]:
        """获取某 session 的全部问答记录（用于 session 结束后分析）。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM interaction_logs WHERE session_id=? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ────────────────────────────────────────────────
    # sync_metadata 表操作
    # ────────────────────────────────────────────────

    def set_sync_status(
        self,
        source: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """记录外部数据源（Brightspace / Google Calendar）的同步状态。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sync_metadata (source, last_sync, status, details)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?)
                """,
                (source, status, json.dumps(details or {}, ensure_ascii=False)),
            )
