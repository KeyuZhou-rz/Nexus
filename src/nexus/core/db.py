"""
SQLite runtime database: connections, the shared schema ledger, and academic state.

One ``nexus.db`` file and one migration ledger serve the whole runtime;
``knowledge.sqlite_store.SQLiteStore`` extends ``AcademicStore`` with
document, concept and mastery tables.

每次连接时强制开启 WAL 模式（并发安全）和外键约束。
所有写入操作均为幂等设计（INSERT OR REPLACE / INSERT OR IGNORE）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ..core.config import ensure_private_runtime_dir
from ..core.connection_registry import register_connection, unregister_connection


# schema.sql 与本文件同目录
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_LATEST_SCHEMA_VERSION = 13
_INBOX_CHANGE_KINDS = {"created", "due_changed", "disappeared"}
_SQLITE_BUSY_TIMEOUT_MS = 30_000


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _as_json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _flag(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return 1 if value else 0


def _stable_key(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _prepare_private_sqlite_path(db_path: Path) -> None:
    """Create or restrict a SQLite file without following a final symlink."""
    ensure_private_runtime_dir(db_path.parent)
    flags = os.O_RDWR | os.O_CREAT
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    elif db_path.is_symlink():  # pragma: no cover - POSIX supplies O_NOFOLLOW
        raise ValueError(f"SQLite database path must not be a symlink: {db_path}")

    try:
        descriptor = os.open(db_path, flags, 0o600)
    except OSError as exc:
        if db_path.is_symlink():
            raise ValueError(
                f"SQLite database path must not be a symlink: {db_path}"
            ) from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"SQLite database path must be a regular file: {db_path}")
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _connect(db_path: Path) -> sqlite3.Connection:
    """
    创建 SQLite 连接并配置必要的 PRAGMA。
    - WAL 模式：允许并发读写，防止写入中断导致数据损坏
    - foreign_keys=ON：确保 CASCADE DELETE 生效
    每次连接都必须设置，SQLite PRAGMA 不持久化到文件。
    """
    db_path = Path(db_path).expanduser()
    _prepare_private_sqlite_path(db_path)
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
    from ..core.config import default_config
    return default_config().data_dir / "nexus.db"


class AcademicStore:
    """
    Nexus 结构化元数据存储（学业状态部分）。
    db_path: SQLite 文件路径，通常为 data/nexus.db
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self._init_schema()

    @contextmanager
    def _connection(self, connection=None):
        if connection is not None:
            yield connection
        else:
            with connect(self.db_path) as conn:
                yield conn

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
            columns = AcademicStore._table_columns(conn, "parse_cache")
            legacy_tables = [
                name for name in ("parse_cache_legacy", "parse_cache_migration_source")
                if AcademicStore._table_exists(conn, name)
            ]
            if required <= columns and not legacy_tables:
                return
            AcademicStore._rebuild_parse_cache(conn, legacy_tables)
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
            columns = AcademicStore._table_columns(conn, "active_sessions")
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
            columns = AcademicStore._table_columns(conn, "chunks")
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
        if version == 8:
            columns = AcademicStore._table_columns(conn, "interaction_logs")
            if "call_site" not in columns:
                conn.execute(
                    "ALTER TABLE interaction_logs ADD COLUMN call_site TEXT NOT NULL DEFAULT 'unspecified'"
                )
            conn.execute(
                """UPDATE interaction_logs
                   SET call_site = COALESCE(call_site, 'unspecified')
                   WHERE call_site IS NULL OR call_site=''"""
            )
            return
        if version == 9:
            columns = AcademicStore._table_columns(conn, "exam_records")
            if "pre_exam_mastery_snapshot" not in columns:
                conn.execute("ALTER TABLE exam_records ADD COLUMN pre_exam_mastery_snapshot TEXT")
            if "pre_exam_snapshot_at" not in columns:
                conn.execute("ALTER TABLE exam_records ADD COLUMN pre_exam_snapshot_at TIMESTAMP")
            return
        if version == 10:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS obligations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    course TEXT,
                    kind TEXT NOT NULL,
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    source_ids TEXT NOT NULL DEFAULT '[]',
                    primary_task_id TEXT,
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obligations_due ON obligations(due_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obligations_course ON obligations(course)"
            )
            return
        if version == 11:
            columns = AcademicStore._table_columns(conn, "obligations")
            for name, definition in (
                ("estimated_hours", "REAL"),
                ("spent_hours", "REAL"),
                ("remaining_hours", "REAL"),
                ("risk_band", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE obligations ADD COLUMN {name} {definition}")
            return
        if version == 12:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS obligation_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    obligation_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obligation_changes_created "
                "ON obligation_changes(created_at)"
            )
            return
        if version == 13:
            columns = AcademicStore._table_columns(conn, "obligations")
            for name, definition in (
                ("progress_percent", "INTEGER"),
                ("remaining_override_hours", "REAL"),
                ("course_id", "TEXT"),
                ("next_step", "TEXT"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE obligations ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_obligations_status_due "
                "ON obligations(status, due_at)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS courses (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS obligation_sources (
                    obligation_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_hash TEXT,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (obligation_id, source, external_id)
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_obligation_sources_external
                   ON obligation_sources(source, external_id)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS calendar_blocks (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    course_id TEXT,
                    kind TEXT NOT NULL DEFAULT 'busy',
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    observed_at TEXT NOT NULL,
                    UNIQUE(source, external_id)
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_calendar_blocks_interval
                   ON calendar_blocks(starts_at, ends_at)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS availability_rules (
                    id TEXT PRIMARY KEY,
                    weekday INTEGER NOT NULL,
                    starts_local TEXT NOT NULL,
                    ends_local TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS availability_overrides (
                    id TEXT PRIMARY KEY,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS state_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_state_changes_created "
                "ON state_changes(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_state_changes_entity "
                "ON state_changes(entity_type, entity_id)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS action_events (
                    id TEXT PRIMARY KEY,
                    obligation_id TEXT NOT NULL,
                    recommendation_key TEXT,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT,
                    idempotency_key TEXT UNIQUE
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_action_events_obligation
                   ON action_events(obligation_id, occurred_at)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS effort_samples (
                    id TEXT PRIMARY KEY,
                    obligation_id TEXT NOT NULL,
                    course_id TEXT,
                    kind TEXT,
                    minutes REAL NOT NULL,
                    completed_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_effort_samples_kind "
                "ON effort_samples(kind)"
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS academic_state_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
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
        if not AcademicStore._table_exists(conn, table):
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
        columns = AcademicStore._table_columns(conn, source)
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
        AcademicStore._create_parse_cache_table(conn, target)
        sources = ["parse_cache", *legacy_tables]
        for source in sources:
            if AcademicStore._table_exists(conn, source):
                AcademicStore._copy_parse_cache_rows(conn, source, target)
        for source in sources:
            if AcademicStore._table_exists(conn, source):
                conn.execute(f'DROP TABLE "{source}"')
        conn.execute(f'ALTER TABLE "{target}" RENAME TO parse_cache')

    @property
    def schema_version(self) -> int:
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])


    @staticmethod
    def _upsert_obligation_row(
        conn: sqlite3.Connection,
        *,
        obligation_id: str,
        title: str,
        course: str | None,
        kind: str,
        due_at: str | None,
        status: str,
        source_ids: list[str],
        primary_task_id: str | None,
        estimated_hours: float | None = None,
        spent_hours: float | None = None,
        remaining_hours: float | None = None,
        risk_band: str | None = None,
        progress_percent: int | None = None,
        remaining_override_hours: float | None = None,
        course_id: str | None = None,
        next_step: str | None = None,
        now: str,
    ) -> None:
        payload = json.dumps(list(source_ids), ensure_ascii=False)
        conn.execute(
            """INSERT INTO obligations(
                   id, title, course, kind, due_at, status,
                   source_ids, primary_task_id,
                   estimated_hours, spent_hours, remaining_hours, risk_band,
                   progress_percent, remaining_override_hours, course_id, next_step,
                   last_seen_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   title=excluded.title,
                   course=excluded.course,
                   kind=excluded.kind,
                   due_at=excluded.due_at,
                   status=excluded.status,
                   source_ids=excluded.source_ids,
                   primary_task_id=excluded.primary_task_id,
                   estimated_hours=excluded.estimated_hours,
                   spent_hours=excluded.spent_hours,
                   remaining_hours=excluded.remaining_hours,
                   risk_band=excluded.risk_band,
                   progress_percent=COALESCE(excluded.progress_percent, obligations.progress_percent),
                   remaining_override_hours=COALESCE(
                       excluded.remaining_override_hours, obligations.remaining_override_hours
                   ),
                   course_id=COALESCE(excluded.course_id, obligations.course_id),
                   next_step=COALESCE(excluded.next_step, obligations.next_step),
                   last_seen_at=excluded.last_seen_at,
                   updated_at=excluded.updated_at""",
            (
                obligation_id, title, course, kind, due_at, status,
                payload, primary_task_id,
                estimated_hours, spent_hours, remaining_hours, risk_band,
                progress_percent, remaining_override_hours, course_id, next_step,
                now, now,
            ),
        )

    def upsert_obligation(
        self,
        *,
        obligation_id: str,
        title: str,
        course: str | None,
        kind: str,
        due_at: str | None,
        status: str,
        source_ids: list[str],
        primary_task_id: str | None,
        estimated_hours: float | None = None,
        spent_hours: float | None = None,
        remaining_hours: float | None = None,
        risk_band: str | None = None,
        progress_percent: int | None = None,
        remaining_override_hours: float | None = None,
        course_id: str | None = None,
        next_step: str | None = None,
    ) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with connect(self.db_path) as conn:
            self._upsert_obligation_row(
                conn,
                obligation_id=obligation_id,
                title=title,
                course=course,
                kind=kind,
                due_at=due_at,
                status=status,
                source_ids=source_ids,
                primary_task_id=primary_task_id,
                estimated_hours=estimated_hours,
                spent_hours=spent_hours,
                remaining_hours=remaining_hours,
                risk_band=risk_band,
                progress_percent=progress_percent,
                remaining_override_hours=remaining_override_hours,
                course_id=course_id,
                next_step=next_step,
                now=now,
            )

    def get_obligation(self, obligation_id: str, *, connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        with self._connection(connection) as conn:
            row = conn.execute(
                "SELECT * FROM obligations WHERE id = ?",
                (obligation_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_obligations(self, *, statuses: list[str] | None = None, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        with self._connection(connection) as conn:
            if statuses:
                placeholders = ",".join("?" * len(statuses))
                rows = conn.execute(
                    f"SELECT * FROM obligations WHERE status IN ({placeholders}) "
                    "ORDER BY due_at IS NULL, due_at",
                    statuses,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM obligations ORDER BY due_at IS NULL, due_at"
                ).fetchall()
            return [dict(row) for row in rows]

    def record_obligation_change(
        self,
        *,
        obligation_id: str,
        kind: str,
        detail: str | None = None,
    ) -> None:
        created = time.strftime("%Y-%m-%dT%H:%M:%S")
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO obligation_changes(
                       obligation_id, kind, detail, created_at
                   ) VALUES (?, ?, ?, ?)""",
                (obligation_id, kind, detail, created),
            )

    def list_obligation_changes(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM obligation_changes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _bump_revision(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM academic_state_meta WHERE key = 'state_revision'"
        ).fetchone()
        current = int(row["value"]) if row else 0
        new = current + 1
        conn.execute(
            """INSERT INTO academic_state_meta(key, value) VALUES ('state_revision', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(new),),
        )
        return new

    def bump_state_revision(self) -> int:
        with connect(self.db_path) as conn:
            return self._bump_revision(conn)

    def get_state_revision(self) -> int:
        raw = self.get_meta("state_revision")
        if raw is None or raw == "":
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def set_meta(self, key: str, value: str, *, connection: sqlite3.Connection | None = None) -> None:
        with self._connection(connection) as conn:
            conn.execute(
                """INSERT INTO academic_state_meta(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, str(value)),
            )

    def get_meta(self, key: str, *, connection: sqlite3.Connection | None = None) -> str | None:
        with self._connection(connection) as conn:
            row = conn.execute(
                "SELECT value FROM academic_state_meta WHERE key = ?",
                (key,),
            ).fetchone()
            return None if row is None else str(row["value"])

    def write_obligation_ingest(
        self,
        *,
        obligation_id: str,
        title: str,
        course: str | None,
        kind: str,
        due_at: str | None,
        status: str,
        source_ids: list[str],
        primary_task_id: str | None,
        estimated_hours: float | None = None,
        spent_hours: float | None = None,
        remaining_hours: float | None = None,
        risk_band: str | None = None,
        progress_percent: int | None = None,
        remaining_override_hours: float | None = None,
        course_id: str | None = None,
        next_step: str | None = None,
        sources: list[dict[str, Any]],
        change_kind: str | None,
        change_detail: str | None,
        state_before: dict[str, Any] | None,
        state_after: dict[str, Any] | None,
        now_iso: str,
        ingest_source: str = "ingest",
        connection: sqlite3.Connection | None = None,
    ) -> int:
        """Upsert obligation + sources + obligation_changes + state_changes + bump revision."""
        with self._connection(connection) as conn:
            self._upsert_obligation_row(
                conn,
                obligation_id=obligation_id,
                title=title,
                course=course,
                kind=kind,
                due_at=due_at,
                status=status,
                source_ids=source_ids,
                primary_task_id=primary_task_id,
                estimated_hours=estimated_hours,
                spent_hours=spent_hours,
                remaining_hours=remaining_hours,
                risk_band=risk_band,
                progress_percent=progress_percent,
                remaining_override_hours=remaining_override_hours,
                course_id=course_id,
                next_step=next_step,
                now=now_iso,
            )
            if sources:
                conn.execute(
                    "UPDATE obligation_sources SET is_primary = 0 WHERE obligation_id = ?",
                    (obligation_id,),
                )
                for item in sources:
                    conn.execute(
                        """INSERT INTO obligation_sources(
                               obligation_id, source, external_id, observed_at,
                               payload_hash, is_primary
                           ) VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(obligation_id, source, external_id) DO UPDATE SET
                               observed_at = excluded.observed_at,
                               payload_hash = excluded.payload_hash,
                               is_primary = excluded.is_primary""",
                        (
                            obligation_id,
                            str(item.get("source") or "unknown"),
                            str(item.get("external_id") or ""),
                            str(item.get("observed_at") or now_iso),
                            item.get("payload_hash"),
                            _flag(item.get("is_primary")),
                        ),
                    )
            if change_kind in _INBOX_CHANGE_KINDS:
                conn.execute(
                    """INSERT INTO obligation_changes(
                           obligation_id, kind, detail, created_at
                       ) VALUES (?, ?, ?, ?)""",
                    (obligation_id, change_kind, change_detail, now_iso),
                )
            if change_kind:
                conn.execute(
                    """INSERT INTO state_changes(
                           entity_type, entity_id, kind, before_json, after_json,
                           created_at, source
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "obligation",
                        obligation_id,
                        change_kind,
                        _as_json_text(state_before),
                        _as_json_text(state_after),
                        now_iso,
                        ingest_source,
                    ),
                )
            return self._bump_revision(conn)

    def upsert_course(
        self,
        course_id: str,
        name: str,
        *,
        active: bool = True,
        now_iso: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        stamp = now_iso or _now_iso()
        with self._connection(connection) as conn:
            conn.execute(
                """INSERT INTO courses(id, name, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name = excluded.name,
                       active = excluded.active,
                       updated_at = excluded.updated_at""",
                (course_id, name, _flag(active, 1), stamp, stamp),
            )
            row = conn.execute(
                "SELECT * FROM courses WHERE id = ?", (course_id,)
            ).fetchone()
            return dict(row)

    def list_courses(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM courses ORDER BY name"
            ).fetchall()
            return [dict(row) for row in rows]

    def list_obligation_sources(self, obligation_id: str | None = None, *, connection: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        with self._connection(connection) as conn:
            rows = conn.execute(
                "SELECT * FROM obligation_sources "
                + ("WHERE obligation_id = ? " if obligation_id else "")
                + "ORDER BY is_primary DESC, source, external_id",
                (obligation_id,) if obligation_id else (),
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_calendar_block(self, *, connection: sqlite3.Connection | None = None, **fields: Any) -> dict[str, Any]:
        source = str(fields["source"])
        external_id = str(fields["external_id"])
        block_id = fields.get("id") or f"cal:{_stable_key(f'{source}|{external_id}')}"
        observed_at = fields.get("observed_at") or _now_iso()
        with self._connection(connection) as conn:
            conn.execute(
                """INSERT INTO calendar_blocks(
                       id, source, external_id, title, starts_at, ends_at,
                       course_id, kind, status, observed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source, external_id) DO UPDATE SET
                       title = excluded.title,
                       starts_at = excluded.starts_at,
                       ends_at = excluded.ends_at,
                       course_id = excluded.course_id,
                       kind = excluded.kind,
                       status = excluded.status,
                       observed_at = excluded.observed_at""",
                (
                    block_id,
                    source,
                    external_id,
                    str(fields["title"]),
                    str(fields["starts_at"]),
                    str(fields["ends_at"]),
                    fields.get("course_id"),
                    str(fields.get("kind") or "busy"),
                    str(fields.get("status") or "confirmed"),
                    observed_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM calendar_blocks WHERE source = ? AND external_id = ?",
                (source, external_id),
            ).fetchone()
            return dict(row)

    def list_calendar_blocks(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM calendar_blocks ORDER BY starts_at, ends_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_calendar_block(self, block_id: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM calendar_blocks WHERE id = ?", (block_id,))

    def upsert_availability_rule(self, **fields: Any) -> dict[str, Any]:
        rule_id = str(fields.get("id") or fields.get("rule_id") or f"ar:{uuid4().hex}")
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO availability_rules(
                       id, weekday, starts_local, ends_local, timezone, enabled
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       weekday = excluded.weekday,
                       starts_local = excluded.starts_local,
                       ends_local = excluded.ends_local,
                       timezone = excluded.timezone,
                       enabled = excluded.enabled""",
                (
                    rule_id,
                    int(fields["weekday"]),
                    str(fields["starts_local"]),
                    str(fields["ends_local"]),
                    str(fields["timezone"]),
                    _flag(fields.get("enabled"), 1),
                ),
            )
            row = conn.execute(
                "SELECT * FROM availability_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            return dict(row)

    def list_availability_rules(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM availability_rules ORDER BY weekday, starts_local"
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_availability_rule(self, rule_id: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM availability_rules WHERE id = ?", (rule_id,))

    def upsert_availability_override(self, **fields: Any) -> dict[str, Any]:
        override_id = str(
            fields.get("id") or fields.get("override_id") or f"ao:{uuid4().hex}"
        )
        with connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO availability_overrides(
                       id, starts_at, ends_at, kind, note
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       starts_at = excluded.starts_at,
                       ends_at = excluded.ends_at,
                       kind = excluded.kind,
                       note = excluded.note""",
                (
                    override_id,
                    str(fields["starts_at"]),
                    str(fields["ends_at"]),
                    str(fields["kind"]),
                    fields.get("note"),
                ),
            )
            row = conn.execute(
                "SELECT * FROM availability_overrides WHERE id = ?",
                (override_id,),
            ).fetchone()
            return dict(row)

    def list_availability_overrides(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM availability_overrides ORDER BY starts_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_availability_override(self, override_id: str) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM availability_overrides WHERE id = ?", (override_id,)
            )

    def record_state_change(
        self,
        *,
        entity_type: str,
        entity_id: str,
        kind: str,
        before_json: Any,
        after_json: Any,
        source: str,
        created_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        stamp = created_at or _now_iso()
        with self._connection(connection) as conn:
            conn.execute(
                """INSERT INTO state_changes(
                       entity_type, entity_id, kind, before_json, after_json,
                       created_at, source
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_type,
                    entity_id,
                    kind,
                    _as_json_text(before_json),
                    _as_json_text(after_json),
                    stamp,
                    source,
                ),
            )

    def list_state_changes(
        self,
        *,
        limit: int = 50,
        entity_id: str | None = None,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM state_changes {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def insert_action_event(
        self,
        *,
        event_id: str,
        obligation_id: str,
        event_type: str,
        occurred_at: str,
        payload_json: Any = None,
        recommendation_key: str | None = None,
        idempotency_key: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO action_events(
                       id, obligation_id, recommendation_key, event_type,
                       occurred_at, payload_json, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    obligation_id,
                    recommendation_key,
                    event_type,
                    occurred_at,
                    _as_json_text(payload_json),
                    idempotency_key,
                ),
            )
            return cursor.rowcount > 0

    def list_action_events(
        self,
        *,
        obligation_id: str | None = None,
        limit: int = 100,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        with self._connection(connection) as conn:
            if obligation_id is not None:
                rows = conn.execute(
                    """SELECT * FROM action_events
                       WHERE obligation_id = ?
                       ORDER BY julianday(occurred_at) DESC, rowid DESC LIMIT ?""",
                    (obligation_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM action_events ORDER BY julianday(occurred_at) DESC, rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def list_latest_action_events(self) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT * FROM (
                       SELECT *, ROW_NUMBER() OVER (
                           PARTITION BY obligation_id ORDER BY julianday(occurred_at) DESC, rowid DESC
                       ) AS latest FROM action_events
                   ) WHERE latest = 1 ORDER BY julianday(occurred_at) DESC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def insert_effort_sample(
        self,
        *,
        sample_id: str,
        obligation_id: str,
        minutes: float,
        completed_at: str,
        source: str,
        course_id: str | None = None,
        kind: str | None = None,
        idempotency_key: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        with self._connection(connection) as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO effort_samples(
                       id, obligation_id, course_id, kind, minutes,
                       completed_at, source, idempotency_key
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sample_id,
                    obligation_id,
                    course_id,
                    kind,
                    float(minutes),
                    completed_at,
                    source,
                    idempotency_key,
                ),
            )
            return cursor.rowcount > 0

    def list_effort_samples(
        self,
        *,
        obligation_id: str | None = None,
        kind: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if obligation_id is not None:
            clauses.append("obligation_id = ?")
            params.append(obligation_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection(connection) as conn:
            rows = conn.execute(
                f"SELECT * FROM effort_samples {where} ORDER BY completed_at DESC",
                params,
            ).fetchall()
            return [dict(row) for row in rows]
