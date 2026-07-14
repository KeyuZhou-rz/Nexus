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
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

from .connection_registry import register_connection, unregister_connection


# schema.sql 与本文件同目录
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_LATEST_SCHEMA_VERSION = 7
_SQLITE_BUSY_TIMEOUT_MS = 30_000


def _connect(db_path: Path) -> sqlite3.Connection:
    """
    创建 SQLite 连接并配置必要的 PRAGMA。
    - WAL 模式：允许并发读写，防止写入中断导致数据损坏
    - foreign_keys=ON：确保 CASCADE DELETE 生效
    每次连接都必须设置，SQLite PRAGMA 不持久化到文件。
    """
    conn = sqlite3.connect(
        str(db_path), timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000
    )
    conn.row_factory = sqlite3.Row          # 让结果可按列名访问
    try:
        conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        deadline = time.monotonic() + _SQLITE_BUSY_TIMEOUT_MS / 1000
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        conn.close()
        raise
    return conn


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection and always close it after the operation."""
    conn = _connect(db_path)
    token = register_connection("sqlite", db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
        unregister_connection("sqlite", db_path, token)


def default_db_path() -> Path:
    """默认 SQLite 路径，基于 config.data_dir 推导绝对路径，避免硬编码相对路径。"""
    from ..config import default_config
    return default_config().data_dir / "nexus.db"


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
        """Create and validate schema while holding a cross-process write lock."""
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with connect(self.db_path) as conn:
            # executescript commits pending work before running. Put BEGIN in
            # the script itself so schema creation and migrations share one
            # exclusive transaction across processes.
            conn.executescript(f"BEGIN EXCLUSIVE;\n{sql}")
            for version in range(1, _LATEST_SCHEMA_VERSION + 1):
                # Version rows are audit history, not proof that every column
                # survived an older interrupted/non-transactional migration.
                self._apply_migration(conn, version)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                    (version,),
                )

    @staticmethod
    def _apply_migration(conn: sqlite3.Connection, version: int) -> None:
        """Apply migrations that cannot be expressed with CREATE IF NOT EXISTS."""
        if version == 1:
            document_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(documents)")
            }
            if "status_updated_at" not in document_columns:
                conn.execute("ALTER TABLE documents ADD COLUMN status_updated_at TIMESTAMP")
            conn.execute(
                """UPDATE documents
                   SET status_updated_at = COALESCE(status_updated_at, parsed_at, CURRENT_TIMESTAMP)"""
            )
            return
        if version == 2:
            required = {
                "file_hash", "course_id", "parser_version", "model_version",
                "prompt_hash", "parse_method", "chunks_json", "file_size",
                "created_at",
            }
            columns = SQLiteStore._table_columns(conn, "parse_cache")
            legacy_tables = [
                name for name in ("parse_cache_legacy", "parse_cache_migration_source")
                if SQLiteStore._table_exists(conn, name)
            ]
            if required <= columns and not legacy_tables:
                return
            SQLiteStore._rebuild_parse_cache(conn, legacy_tables)
            return
        if version == 3:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(learning_events)")
            }
            for name, column_type in (
                ("evidence_type", "TEXT"),
                ("trust_status", "TEXT"),
                ("classified_at", "TIMESTAMP"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE learning_events ADD COLUMN {name} {column_type}"
                    )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_learning_events_trust
                   ON learning_events(trust_status, concept_id, timestamp)"""
            )
            return
        if version == 4:
            columns = SQLiteStore._table_columns(conn, "active_sessions")
            for name, definition in (
                ("finalize_status", "TEXT NOT NULL DEFAULT 'active'"),
                ("finalize_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("finalize_last_error", "TEXT"),
                ("finalize_next_retry", "TIMESTAMP"),
                ("finalize_claimed_at", "TIMESTAMP"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE active_sessions ADD COLUMN {name} {definition}")
            conn.execute(
                """UPDATE active_sessions SET finalize_status = CASE
                       WHEN ended=1 THEN 'processing' ELSE 'active' END
                   WHERE finalize_status IS NULL OR finalize_status=''"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_active_sessions_finalize
                   ON active_sessions(ended, finalize_status, finalize_next_retry)"""
            )
            return
        if version == 5:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assessment_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    course_id TEXT,
                    concept_id TEXT NOT NULL,
                    concept_name TEXT NOT NULL,
                    question TEXT NOT NULL,
                    source_refs TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'awaiting_answer',
                    student_answer TEXT,
                    event_type TEXT,
                    confidence REAL,
                    feedback TEXT,
                    recorded INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    submitted_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (concept_id) REFERENCES concepts(concept_id) ON DELETE CASCADE
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_assessment_session_status
                   ON assessment_attempts(session_id, status, created_at DESC)"""
            )
            return
        if version == 6:
            columns = SQLiteStore._table_columns(conn, "chunks")
            if "chunk_index" not in columns:
                conn.execute("ALTER TABLE chunks ADD COLUMN chunk_index INTEGER")
            return
        if version == 7:
            # concept_quarantine table is defined in schema.sql (CREATE IF NOT EXISTS)
            # Migration ensures the table exists for DBs that skip schema re-init.
            conn.execute(
                """CREATE TABLE IF NOT EXISTS concept_quarantine (
                    concept_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    definition TEXT,
                    concept_type TEXT,
                    quarantine_reason TEXT NOT NULL,
                    original_data TEXT,
                    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            return
        raise RuntimeError(f"Unsupported schema migration: {version}")

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        if not SQLiteStore._table_exists(conn, table):
            return set()
        return {row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')}

    @staticmethod
    def _create_parse_cache_table(conn: sqlite3.Connection, table: str) -> None:
        conn.execute(
            f'''CREATE TABLE "{table}" (
                   file_hash TEXT NOT NULL,
                   course_id TEXT NOT NULL DEFAULT '',
                   parser_version TEXT NOT NULL,
                   model_version TEXT NOT NULL,
                   prompt_hash TEXT NOT NULL,
                   parse_method TEXT NOT NULL,
                   chunks_json TEXT NOT NULL,
                   file_size INTEGER,
                   created_at TEXT NOT NULL DEFAULT (datetime('now')),
                   PRIMARY KEY (
                       file_hash, course_id, parser_version,
                       model_version, prompt_hash
                   )
               )'''
        )

    @staticmethod
    def _copy_parse_cache_rows(
        conn: sqlite3.Connection, source: str, target: str
    ) -> None:
        columns = SQLiteStore._table_columns(conn, source)
        if not {"file_hash", "chunks_json"} <= columns:
            return

        def value(column: str, fallback: str) -> str:
            return f'COALESCE("{column}", {fallback})' if column in columns else fallback

        expressions = [
            '"file_hash"',
            value("course_id", "''"),
            value("parser_version", "'legacy'"),
            value("model_version", "'legacy'"),
            value("prompt_hash", "'legacy'"),
            value("parse_method", "'deepseek'"),
            '"chunks_json"',
            '"file_size"' if "file_size" in columns else "NULL",
            value("created_at", "datetime('now')"),
        ]
        conn.execute(
            f'''INSERT OR IGNORE INTO "{target}"
                   (file_hash, course_id, parser_version, model_version,
                    prompt_hash, parse_method, chunks_json, file_size, created_at)
                SELECT {", ".join(expressions)} FROM "{source}"'''
        )

    @staticmethod
    def _rebuild_parse_cache(
        conn: sqlite3.Connection, legacy_tables: list[str]
    ) -> None:
        target = "parse_cache_v2_new"
        conn.execute(f'DROP TABLE IF EXISTS "{target}"')
        SQLiteStore._create_parse_cache_table(conn, target)
        sources = ["parse_cache", *legacy_tables]
        for source in sources:
            if SQLiteStore._table_exists(conn, source):
                SQLiteStore._copy_parse_cache_rows(conn, source, target)
        for source in sources:
            if SQLiteStore._table_exists(conn, source):
                conn.execute(f'DROP TABLE "{source}"')
        conn.execute(f'ALTER TABLE "{target}" RENAME TO parse_cache')

    @property
    def schema_version(self) -> int:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])

    # ────────────────────────────────────────────────
    # documents 表操作
    # ────────────────────────────────────────────────

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        """按 id 查询文档记录，不存在则返回 None。"""
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (id, course_id, filename, file_hash, parse_status, chunk_count,
                     parse_model, status_updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    course_id=excluded.course_id,
                    filename=excluded.filename,
                    file_hash=excluded.file_hash,
                    parse_status=excluded.parse_status,
                    chunk_count=excluded.chunk_count,
                    parse_model=excluded.parse_model,
                    status_updated_at=CURRENT_TIMESTAMP
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
        with connect(self.db_path) as conn:
            if chunk_count is not None:
                conn.execute(
                    """UPDATE documents SET parse_status=?, chunk_count=?,
                       status_updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (status, chunk_count, doc_id),
                )
            else:
                conn.execute(
                    """UPDATE documents SET parse_status=?,
                       status_updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (status, doc_id),
                )

    def recover_stale_parsing(self, older_than: timedelta = timedelta(hours=1)) -> int:
        """Return interrupted parsing documents to a retryable pending state."""
        if older_than.total_seconds() < 0:
            raise ValueError("older_than must not be negative")
        cutoff = f"-{older_than.total_seconds()} seconds"
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE documents
                   SET parse_status='pending', status_updated_at=CURRENT_TIMESTAMP
                   WHERE parse_status='parsing'
                     AND COALESCE(status_updated_at, parsed_at) <= datetime('now', ?)""",
                (cutoff,),
            )
            return cursor.rowcount

    def delete_document(self, doc_id: str) -> None:
        """
        删除文档记录，关联 chunks 因 CASCADE 自动删除。
        文件更新后重新 ingest 时调用。
        """
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))

    def get_chunks_by_course(self, course_id: str) -> list[dict[str, Any]]:
        """查询指定课程的所有 chunk 元数据（不含 content）。"""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE course_id = ? ORDER BY chunk_id",
                (course_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ────────────────────────────────────────────────
    # user_profile 表操作
    # ────────────────────────────────────────────────

    def get_profile(self, key: str = "profile") -> dict[str, Any]:
        """读取用户画像，不存在时返回空字典。"""
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_profile (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (key, json.dumps(data, ensure_ascii=False)),
            )

    # ────────────────────────────────────────────────
    # active_sessions 表操作（Session 状态持久化）
    # ────────────────────────────────────────────────

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """读取单个 session 状态，不存在返回 None。"""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM active_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def upsert_session(
        self,
        session_id: str,
        started_at: str,
        last_active: str,
        course_id: str | None,
        interaction_count: int,
        ended: bool,
    ) -> None:
        """插入或覆盖单个 session 状态（整体替换）。"""
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO active_sessions
                    (session_id, started_at, last_active, course_id, interaction_count, ended)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, started_at, last_active, course_id, interaction_count, int(ended)),
            )

    def delete_session(self, session_id: str) -> None:
        """删除 session 状态（session 结束后清理，防止表无限增长）。"""
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM active_sessions WHERE session_id=?", (session_id,))

    def claim_session_end(self, session_id: str) -> bool:
        """Atomically claim an active session for finalization.

        Returns False when another process already claimed or ended it. This
        prevents two scheduler instances from applying the same profile patch.
        """
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE active_sessions
                   SET ended=1, finalize_status='processing',
                       finalize_attempts=finalize_attempts+1,
                       finalize_claimed_at=CURRENT_TIMESTAMP,
                       finalize_last_error=NULL
                   WHERE session_id=? AND ended=0
                     AND (finalize_next_retry IS NULL
                          OR datetime(finalize_next_retry) <= datetime('now', 'localtime'))""",
                (session_id,),
            )
            return cursor.rowcount == 1

    def release_session_retry(
        self,
        session_id: str,
        error: str,
        next_retry: str,
    ) -> None:
        """Release a finalize claim after a transient failure."""
        with connect(self.db_path) as conn:
            conn.execute(
                """UPDATE active_sessions
                   SET ended=0, finalize_status='retry', finalize_last_error=?,
                       finalize_next_retry=?, finalize_claimed_at=NULL
                   WHERE session_id=? AND finalize_status='processing'""",
                (error[:1000], next_retry, session_id),
            )

    def abandon_session(self, session_id: str, reason: str) -> bool:
        """Permanently stop retries while retaining finalization audit state."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE active_sessions
                   SET ended=1, finalize_status='abandoned', finalize_last_error=?,
                       finalize_next_retry=NULL, finalize_claimed_at=NULL
                   WHERE session_id=? AND finalize_status != 'abandoned'""",
                (reason[:1000], session_id),
            )
            return cursor.rowcount == 1

    def get_active_sessions(self) -> list[dict[str, Any]]:
        """获取所有未结束（ended=0）的 session，供 idle 超时轮询使用。"""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM active_sessions WHERE ended=0"
            ).fetchall()
            return [dict(r) for r in rows]

    # ────────────────────────────────────────────────
    # assessment_attempts 表操作
    # ────────────────────────────────────────────────

    def create_assessment_attempt(
        self,
        *,
        attempt_id: str,
        session_id: str,
        course_id: str | None,
        concept_id: str,
        concept_name: str,
        question: str,
        source_refs: list[str],
        metadata: dict[str, Any],
    ) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO assessment_attempts
                   (attempt_id, session_id, course_id, concept_id, concept_name,
                    question, source_refs, metadata, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_answer')""",
                (
                    attempt_id,
                    session_id,
                    course_id,
                    concept_id,
                    concept_name,
                    question,
                    json.dumps(source_refs, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

    def get_assessment_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM assessment_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_pending_assessment(self, session_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM assessment_attempts
                   WHERE session_id=? AND status='awaiting_answer'
                   ORDER BY created_at DESC, attempt_id DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
            return dict(row) if row else None

    def claim_assessment_attempt(self, attempt_id: str, student_answer: str) -> bool:
        """Atomically allow exactly one submitter to grade an awaiting attempt."""
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """UPDATE assessment_attempts
                   SET status='grading', student_answer=?, submitted_at=CURRENT_TIMESTAMP
                   WHERE attempt_id=? AND status='awaiting_answer'""",
                (student_answer, attempt_id),
            )
            return cursor.rowcount == 1

    def release_assessment_attempt(self, attempt_id: str) -> bool:
        """Return a failed grading claim to the answerable state."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE assessment_attempts
                   SET status='awaiting_answer', student_answer=NULL, submitted_at=NULL
                   WHERE attempt_id=? AND status='grading'""",
                (attempt_id,),
            )
            return cursor.rowcount == 1

    def complete_assessment_attempt(
        self,
        attempt_id: str,
        *,
        event_type: str,
        confidence: float,
        feedback: str,
        recorded: bool,
    ) -> bool:
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """UPDATE assessment_attempts
                   SET status='completed', event_type=?, confidence=?, feedback=?,
                       recorded=?, completed_at=CURRENT_TIMESTAMP
                   WHERE attempt_id=? AND status='grading'""",
                (event_type, confidence, feedback, int(recorded), attempt_id),
            )
            return cursor.rowcount == 1

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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM concepts WHERE concept_id = ?", (concept_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_concepts_by_course(self, course_id: str) -> list[dict[str, Any]]:
        """查询指定课程的所有概念。"""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM concepts WHERE course_id = ? ORDER BY name",
                (course_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_concepts(self) -> list[dict[str, Any]]:
        """查询所有课程的全部概念（用于跨课程关系发现）。"""
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM concept_aliases WHERE alias = ? AND course_id = ?",
                (alias.strip().lower(), course_id),
            ).fetchone()
            return dict(row) if row else None

    def get_aliases_for_concept(self, concept_id: str) -> list[dict[str, Any]]:
        """获取指定概念的所有别名。"""
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
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
        evidence_type: str = "direct",
        trust_status: str = "trusted",
    ) -> None:
        """记录一次学习事件。"""
        if trust_status not in {"trusted", "legacy"}:
            raise ValueError("trust_status must be 'trusted' or 'legacy'")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO learning_events
                    (concept_id, event_type, session_id, context,
                     evidence_type, trust_status, classified_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    concept_id,
                    event_type,
                    session_id,
                    json.dumps(context, ensure_ascii=False) if context else None,
                    evidence_type,
                    trust_status,
                ),
            )

    def record_mastery_event_atomic(
        self,
        concept_id: str,
        event_type: str,
        *,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
        evidence_type: str = "direct",
        mastery_add: float = 0.0,
        mastery_mult: float = 1.0,
        decay_mult: float = 1.0,
        streak_add: int = 0,
    ) -> dict[str, Any]:
        """Record trusted evidence and update mastery in one write transaction."""
        with connect(self.db_path) as conn:
            # Acquire the write lock before reading concept_mastery so another
            # process cannot calculate an increment from the same old value.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO learning_events
                       (concept_id, event_type, session_id, context,
                        evidence_type, trust_status, classified_at)
                   VALUES (?, ?, ?, ?, ?, 'trusted', CURRENT_TIMESTAMP)""",
                (
                    concept_id,
                    event_type,
                    session_id,
                    json.dumps(context, ensure_ascii=False) if context else None,
                    evidence_type,
                ),
            )
            current = conn.execute(
                "SELECT * FROM concept_mastery WHERE concept_id = ?", (concept_id,)
            ).fetchone()
            initial = float(current["initial_mastery"]) if current else 0.0
            decay = float(current["decay_rate"]) if current else 0.3
            review_count = int(current["review_count"]) if current else 0
            streak = int(current["streak"]) if current else 0

            initial = max(0.0, min(1.0, initial * mastery_mult + mastery_add))
            decay = max(0.01, min(1.0, decay * decay_mult))
            review_count += 1
            streak = 0 if streak_add == -999 else max(0, streak + streak_add)
            conn.execute(
                """INSERT INTO concept_mastery
                       (concept_id, initial_mastery, decay_rate, last_reviewed,
                        review_count, streak)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
                   ON CONFLICT(concept_id) DO UPDATE SET
                       initial_mastery=excluded.initial_mastery,
                       decay_rate=excluded.decay_rate,
                       last_reviewed=CURRENT_TIMESTAMP,
                       review_count=excluded.review_count,
                       streak=excluded.streak""",
                (concept_id, initial, decay, review_count, streak),
            )
            return {
                "concept_id": concept_id,
                "initial_mastery": initial,
                "decay_rate": decay,
                "review_count": review_count,
                "streak": streak,
            }

    def get_learning_events(
        self,
        concept_id: str,
        since: str | None = None,
        trusted_only: bool = False,
    ) -> list[dict[str, Any]]:
        """获取指定概念的学习事件（可选时间过滤）。"""
        with connect(self.db_path) as conn:
            trust_clause = " AND trust_status = 'trusted'" if trusted_only else ""
            if since:
                rows = conn.execute(
                    f"""SELECT * FROM learning_events
                       WHERE concept_id = ? AND timestamp >= ?
                       {trust_clause}
                       ORDER BY timestamp""",
                    (concept_id, since),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT * FROM learning_events WHERE concept_id = ?
                        {trust_clause} ORDER BY timestamp""",
                    (concept_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_learning_event_stats(
        self, course_id: str | None = None
    ) -> dict[str, Any]:
        """Return auditable trusted/legacy/unclassified event counts."""
        with connect(self.db_path) as conn:
            params: tuple[Any, ...] = ()
            course_join = ""
            course_where = ""
            if course_id:
                course_join = " JOIN concepts c ON c.concept_id = le.concept_id"
                course_where = " WHERE c.course_id = ?"
                params = (course_id,)
            rows = conn.execute(
                f"""SELECT COALESCE(le.trust_status, 'unclassified') AS status,
                           COUNT(*) AS count
                    FROM learning_events le{course_join}{course_where}
                    GROUP BY COALESCE(le.trust_status, 'unclassified')""",
                params,
            ).fetchall()
            by_status = {row["status"]: row["count"] for row in rows}
            evidence_rows = conn.execute(
                f"""SELECT COALESCE(le.evidence_type, 'unclassified') AS evidence,
                           COUNT(*) AS count
                    FROM learning_events le{course_join}{course_where}
                    GROUP BY COALESCE(le.evidence_type, 'unclassified')""",
                params,
            ).fetchall()
        total = sum(by_status.values())
        return {
            "total": total,
            "trusted": by_status.get("trusted", 0),
            "legacy": by_status.get("legacy", 0),
            "unclassified": by_status.get("unclassified", 0),
            "by_evidence_type": {
                row["evidence"]: row["count"] for row in evidence_rows
            },
        }

    def upsert_mastery(
        self,
        concept_id: str,
        initial_mastery: float,
        decay_rate: float,
        review_count: int = 0,
        streak: int = 0,
    ) -> None:
        """更新概念掌握度参数。"""
        with connect(self.db_path) as conn:
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
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM concept_mastery WHERE concept_id = ?",
                (concept_id,),
            ).fetchone()
            return dict(row) if row else None

    # ════════════════════════════════════════════════
    # Phase N2: parse_cache 表
    # ════════════════════════════════════════════════

    def get_parse_cache(
        self,
        file_hash: str,
        course_id: str = "",
        *,
        parser_version: str,
        model_version: str,
        prompt_hash: str,
    ) -> list[dict[str, Any]] | None:
        """查询缓存的解析结果，未命中返回 None。"""
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT chunks_json FROM parse_cache
                   WHERE file_hash = ? AND course_id = ? AND parser_version = ?
                     AND model_version = ? AND prompt_hash = ?""",
                (file_hash, course_id, parser_version, model_version, prompt_hash),
            ).fetchone()
            if not row:
                return None
            try:
                data = json.loads(row["chunks_json"])
                if isinstance(data, list):
                    return data
            except (json.JSONDecodeError, TypeError):
                return None
            return None

    def set_parse_cache(
        self,
        file_hash: str,
        course_id: str,
        chunks: list[dict[str, Any]],
        file_size: int = 0,
        *,
        parser_version: str,
        model_version: str,
        prompt_hash: str,
        parse_method: str,
    ) -> None:
        """缓存解析结果（INSERT OR REPLACE，幂等）。"""
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO parse_cache
                    (file_hash, course_id, parser_version, model_version,
                     prompt_hash, parse_method, chunks_json, file_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    file_hash, course_id, parser_version, model_version,
                    prompt_hash, parse_method,
                    json.dumps(chunks, ensure_ascii=False), file_size,
                ),
            )

    def list_parse_cache(self, course_id: str | None = None) -> list[dict[str, Any]]:
        """List cache metadata without loading potentially large chunk payloads."""
        with connect(self.db_path) as conn:
            if course_id:
                rows = conn.execute(
                    """SELECT file_hash, course_id, parser_version, model_version,
                              prompt_hash, parse_method, file_size, created_at,
                              json_array_length(chunks_json) AS chunk_count
                       FROM parse_cache WHERE course_id = ? ORDER BY created_at DESC""",
                    (course_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT file_hash, course_id, parser_version, model_version,
                              prompt_hash, parse_method, file_size, created_at,
                              json_array_length(chunks_json) AS chunk_count
                       FROM parse_cache ORDER BY created_at DESC"""
                ).fetchall()
            return [dict(row) for row in rows]

    def clear_parse_cache(self, course_id: str | None = None) -> int:
        """清除解析缓存。不传 course_id 则全部清除。返回删除行数。"""
        with connect(self.db_path) as conn:
            if course_id:
                cursor = conn.execute(
                    "DELETE FROM parse_cache WHERE course_id = ?", (course_id,)
                )
            else:
                cursor = conn.execute("DELETE FROM parse_cache")
            return cursor.rowcount

    def get_all_mastery(
        self, course_id: str | None = None
    ) -> list[dict[str, Any]]:
        """获取所有（或指定课程的）概念掌握度参数。"""
        with connect(self.db_path) as conn:
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

    # ════════════════════════════════════════════════
    # Phase N: Memory-Layer Enhancement
    # ════════════════════════════════════════════════

    # ── Concept Checkpoints ────────────────────────

    def upsert_checkpoint(
        self, checkpoint_id: str, course_id: str,
        week_start: str, week_end: str,
    ) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO concept_checkpoints
                   (checkpoint_id, course_id, week_start, week_end)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(checkpoint_id) DO UPDATE SET
                   week_start=excluded.week_start,
                   week_end=excluded.week_end,
                   generated_at=CURRENT_TIMESTAMP""",
                (checkpoint_id, course_id, week_start, week_end),
            )

    def upsert_checkpoint_concept(
        self, checkpoint_id: str, concept_id: str | None,
        name: str, importance: int,
        q1: str, q2: str, q3: str,
    ) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO checkpoint_concepts
                   (checkpoint_id, concept_id, name, importance,
                    self_test_q1, self_test_q2, self_test_q3)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (checkpoint_id, concept_id, name, importance, q1, q2, q3),
            )

    def get_latest_checkpoint(
        self, course_id: str,
    ) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM concept_checkpoints
                   WHERE course_id = ?
                   ORDER BY generated_at DESC LIMIT 1""",
                (course_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_checkpoint_concepts(
        self, checkpoint_id: str,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM checkpoint_concepts
                   WHERE checkpoint_id = ?
                   ORDER BY importance DESC""",
                (checkpoint_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Concept Query Log ──────────────────────────

    def log_concept_query(
        self, concept_id: str | None, concept_name: str,
        session_id: str | None, query_text: str,
        had_hesitation: int = 0,
    ) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO concept_query_log
                   (concept_id, concept_name, session_id,
                    query_text, had_hesitation)
                   VALUES (?, ?, ?, ?, ?)""",
                (concept_id, concept_name, session_id,
                 query_text, had_hesitation),
            )

    def get_concept_query_stats(
        self, concept_id: str, since_days: int = 14,
    ) -> dict[str, int]:
        with connect(self.db_path) as conn:
            total = conn.execute(
                """SELECT COUNT(*) FROM concept_query_log
                   WHERE concept_id = ?
                   AND timestamp >= datetime('now', ?)""",
                (concept_id, f'-{since_days} days'),
            ).fetchone()[0]
            hesitations = conn.execute(
                """SELECT COUNT(*) FROM concept_query_log
                   WHERE concept_id = ? AND had_hesitation = 1
                   AND timestamp >= datetime('now', ?)""",
                (concept_id, f'-{since_days} days'),
            ).fetchone()[0]
            return {"total_queries": total, "hesitation_count": hesitations}

    # ── Exam Records ───────────────────────────────

    def upsert_exam_record(
        self, exam_id: str, course_id: str, exam_date: str,
        chapter_weights: str | None = None,
        user_input: str | None = None,
    ) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO exam_records
                   (exam_id, course_id, exam_date,
                    chapter_weights, user_input)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(exam_id) DO UPDATE SET
                   chapter_weights=excluded.chapter_weights,
                   user_input=excluded.user_input""",
                (exam_id, course_id, exam_date,
                 chapter_weights, user_input),
            )

    def get_exam_record(self, exam_id: str) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM exam_records WHERE exam_id = ?",
                (exam_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_exams_for_course(
        self, course_id: str,
    ) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM exam_records
                   WHERE course_id = ?
                   ORDER BY exam_date DESC""",
                (course_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Exam Retrospectives ────────────────────────

    def upsert_retrospective(
        self, retrospective_id: str, exam_id: str,
        predicted_weak: str | None = None,
        actual_hits: str | None = None,
        blind_spots: str | None = None,
        overestimated: str | None = None,
        analysis_text: str | None = None,
        retention_hook: str | None = None,
    ) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO exam_retrospectives
                   (retrospective_id, exam_id, predicted_weak,
                    actual_hits, blind_spots, overestimated,
                    analysis_text, retention_hook)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(retrospective_id) DO UPDATE SET
                   predicted_weak=excluded.predicted_weak,
                   actual_hits=excluded.actual_hits,
                   blind_spots=excluded.blind_spots,
                   overestimated=excluded.overestimated,
                   analysis_text=excluded.analysis_text,
                   retention_hook=excluded.retention_hook,
                   generated_at=CURRENT_TIMESTAMP""",
                (retrospective_id, exam_id, predicted_weak,
                 actual_hits, blind_spots, overestimated,
                 analysis_text, retention_hook),
            )

    def get_retrospective(
        self, retrospective_id: str,
    ) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM exam_retrospectives
                   WHERE retrospective_id = ?""",
                (retrospective_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_retrospective_for_exam(
        self, exam_id: str,
    ) -> dict[str, Any] | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                """SELECT * FROM exam_retrospectives
                   WHERE exam_id = ?
                   ORDER BY generated_at DESC LIMIT 1""",
                (exam_id,),
            ).fetchone()
            return dict(row) if row else None
