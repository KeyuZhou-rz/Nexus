"""
SQLite 存储层 — Nexus Phase 3 + Phase 4 (Knowledge Graph)

Phase 3 表: documents / chunks / user_profile / interaction_logs / sync_metadata
Phase 4 表: concepts / concept_relations / chunk_concepts / learning_events / concept_mastery

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

    # ════════════════════════════════════════════════
    # Phase 4: Knowledge Graph — concepts 表
    # ════════════════════════════════════════════════

    def upsert_concept(
        self,
        concept_id: str,
        name: str,
        course_id: str,
        definition: str | None = None,
        concept_type: str | None = None,
    ) -> None:
        """插入或更新概念节点。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO concepts (concept_id, name, course_id, definition, concept_type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(concept_id) DO UPDATE SET
                    name = excluded.name,
                    definition = COALESCE(excluded.definition, concepts.definition),
                    concept_type = COALESCE(excluded.concept_type, concepts.concept_type),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (concept_id, name, course_id, definition, concept_type),
            )

    def get_concept(self, concept_id: str) -> dict[str, Any] | None:
        """按 ID 查询单个概念。"""
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM concepts WHERE concept_id = ?", (concept_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_concepts_by_course(self, course_id: str) -> list[dict[str, Any]]:
        """查询指定课程的所有概念。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM concepts WHERE course_id = ? ORDER BY name",
                (course_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_concepts(self) -> list[dict[str, Any]]:
        """查询所有课程的全部概念（用于跨课程关系发现）。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM concepts ORDER BY course_id, name"
            ).fetchall()
            return [dict(r) for r in rows]

    # ════════════════════════════════════════════════
    # Phase 4: Knowledge Graph — concept_relations 表
    # ════════════════════════════════════════════════

    def upsert_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        confidence: float = 1.0,
        evidence: str | None = None,
    ) -> None:
        """
        插入或更新概念关系。UNIQUE(source_id, target_id, relation_type) 保证幂等。
        冲突时更新 confidence 和 evidence。
        """
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO concept_relations
                    (source_id, target_id, relation_type, confidence, evidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id, target_id, relation_type) DO UPDATE SET
                    confidence = excluded.confidence,
                    evidence = excluded.evidence
                """,
                (source_id, target_id, relation_type, confidence, evidence),
            )

    def get_relations_for_concept(self, concept_id: str) -> list[dict[str, Any]]:
        """
        查询与指定概念相关的所有关系（双向：作为 source 或 target）。
        同时 JOIN concepts 表获取对端概念信息。
        """
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT cr.*, c.name AS other_name, c.course_id AS other_course,
                       c.definition AS other_definition,
                       CASE WHEN cr.source_id = ? THEN 'outgoing' ELSE 'incoming' END AS direction
                FROM concept_relations cr
                JOIN concepts c ON c.concept_id = CASE
                    WHEN cr.source_id = ? THEN cr.target_id
                    ELSE cr.source_id END
                WHERE cr.source_id = ? OR cr.target_id = ?
                ORDER BY cr.confidence DESC
                """,
                (concept_id, concept_id, concept_id, concept_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_cross_course_relations(
        self, concept_id: str
    ) -> list[dict[str, Any]]:
        """查询指定概念的跨课程关系（source 和 target 属于不同课程）。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT cr.*, c1.name AS source_name, c1.course_id AS source_course,
                       c2.name AS target_name, c2.course_id AS target_course,
                       c2.definition AS target_definition
                FROM concept_relations cr
                JOIN concepts c1 ON c1.concept_id = cr.source_id
                JOIN concepts c2 ON c2.concept_id = cr.target_id
                WHERE (cr.source_id = ? OR cr.target_id = ?)
                  AND c1.course_id != c2.course_id
                ORDER BY cr.confidence DESC
                """,
                (concept_id, concept_id),
            ).fetchall()
            return [dict(r) for r in rows]

    # ════════════════════════════════════════════════
    # Phase M1-3: concept_aliases 表
    # ════════════════════════════════════════════════

    def upsert_alias(
        self,
        alias: str,
        course_id: str,
        concept_id: str,
        source: str = "manual",
        confidence: float = 1.0,
    ) -> None:
        """
        插入或更新概念别名映射。
        UNIQUE(alias, course_id) 约束：同一课程内同一别名只能映射到一个概念。
        """
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO concept_aliases (alias, course_id, concept_id, source, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(alias, course_id) DO UPDATE SET
                    concept_id = excluded.concept_id,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    created_at = CURRENT_TIMESTAMP
                """,
                (alias.strip().lower(), course_id, concept_id, source, confidence),
            )

    def get_alias(
        self, alias: str, course_id: str
    ) -> dict[str, Any] | None:
        """
        按别名精确查找概念映射。
        返回 dict 包含 concept_id, confidence, source 等，未找到返回 None。
        """
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM concept_aliases WHERE alias = ? AND course_id = ?",
                (alias.strip().lower(), course_id),
            ).fetchone()
            return dict(row) if row else None

    def get_aliases_for_concept(self, concept_id: str) -> list[dict[str, Any]]:
        """获取指定概念的所有别名。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM concept_aliases WHERE concept_id = ? ORDER BY alias",
                (concept_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ════════════════════════════════════════════════
    # Phase 4: Knowledge Graph — chunk_concepts 表
    # ════════════════════════════════════════════════

    def link_chunk_to_concept(
        self,
        chunk_id: str,
        concept_id: str,
        relevance: float = 1.0,
    ) -> None:
        """关联 chunk 与 concept（多对多）。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO chunk_concepts (chunk_id, concept_id, relevance)
                VALUES (?, ?, ?)
                """,
                (chunk_id, concept_id, relevance),
            )

    def get_concepts_for_chunks(
        self, chunk_ids: list[str]
    ) -> list[dict[str, Any]]:
        """批量查询：给定 chunk_ids，返回所有关联的概念（含 JOIN）。"""
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT DISTINCT c.*, cc.chunk_id, cc.relevance
                FROM chunk_concepts cc
                JOIN concepts c ON c.concept_id = cc.concept_id
                WHERE cc.chunk_id IN ({placeholders})
                ORDER BY cc.relevance DESC
                """,
                chunk_ids,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chunks_for_concept(self, concept_id: str) -> list[str]:
        """查询与指定概念关联的所有 chunk_id。"""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT chunk_id FROM chunk_concepts WHERE concept_id = ?",
                (concept_id,),
            ).fetchall()
            return [r["chunk_id"] for r in rows]

    # ════════════════════════════════════════════════
    # Phase 4B: Temporal Memory — learning_events / concept_mastery
    # ════════════════════════════════════════════════

    def record_learning_event(
        self,
        concept_id: str,
        event_type: str,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """记录一次学习事件。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO learning_events (concept_id, event_type, session_id, context)
                VALUES (?, ?, ?, ?)
                """,
                (
                    concept_id,
                    event_type,
                    session_id,
                    json.dumps(context, ensure_ascii=False) if context else None,
                ),
            )

    def get_learning_events(
        self,
        concept_id: str,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取指定概念的学习事件（可选时间过滤）。"""
        with _connect(self.db_path) as conn:
            if since:
                rows = conn.execute(
                    """SELECT * FROM learning_events
                       WHERE concept_id = ? AND timestamp >= ?
                       ORDER BY timestamp""",
                    (concept_id, since),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM learning_events WHERE concept_id = ? ORDER BY timestamp",
                    (concept_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def upsert_mastery(
        self,
        concept_id: str,
        initial_mastery: float,
        decay_rate: float,
        review_count: int = 0,
        streak: int = 0,
    ) -> None:
        """更新概念掌握度参数。"""
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO concept_mastery
                    (concept_id, initial_mastery, decay_rate, last_reviewed, review_count, streak)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                ON CONFLICT(concept_id) DO UPDATE SET
                    initial_mastery = excluded.initial_mastery,
                    decay_rate = excluded.decay_rate,
                    last_reviewed = CURRENT_TIMESTAMP,
                    review_count = excluded.review_count,
                    streak = excluded.streak
                """,
                (concept_id, initial_mastery, decay_rate, review_count, streak),
            )

    def get_mastery(self, concept_id: str) -> dict[str, Any] | None:
        """获取概念的掌握度参数。"""
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM concept_mastery WHERE concept_id = ?",
                (concept_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_all_mastery(
        self, course_id: str | None = None
    ) -> list[dict[str, Any]]:
        """获取所有（或指定课程的）概念掌握度参数。"""
        with _connect(self.db_path) as conn:
            if course_id:
                rows = conn.execute(
                    """
                    SELECT cm.*, c.name, c.course_id
                    FROM concept_mastery cm
                    JOIN concepts c ON c.concept_id = cm.concept_id
                    WHERE c.course_id = ?
                    """,
                    (course_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT cm.*, c.name, c.course_id
                    FROM concept_mastery cm
                    JOIN concepts c ON c.concept_id = cm.concept_id
                    """
                ).fetchall()
            return [dict(r) for r in rows]
