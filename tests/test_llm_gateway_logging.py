from __future__ import annotations

import sqlite3
from pathlib import Path

from nexus.knowledge.sqlite_store import SQLiteStore, connect


def test_log_interaction_persists_call_site(tmp_path: Path) -> None:
    db_path = tmp_path / "nexus.db"
    store = SQLiteStore(db_path)
    store.log_interaction(
        session_id="s1",
        user_query="q1",
        llm_response="a1",
        course_id="CS202_OS",
        call_site="qa_client",
    )

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT session_id, user_query, call_site FROM interaction_logs"
        ).fetchone()
        assert row["session_id"] == "s1"
        assert row["user_query"] == "q1"
        assert row["call_site"] == "qa_client"


def test_legacy_database_gets_call_site_column_and_backfills(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            [(1,), (2,), (3,), (4,), (5,), (6,), (7,)],
        )
        conn.execute(
            """CREATE TABLE interaction_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT NOT NULL,
                course_id TEXT,
                user_query TEXT NOT NULL,
                chunks_used TEXT,
                llm_response TEXT,
                model_used TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER
            )"""
        )
        conn.execute(
            """INSERT INTO interaction_logs
                   (session_id, course_id, user_query, llm_response, model_used)
               VALUES ('s-legacy', 'CS202_OS', 'legacy-q', 'legacy-a', 'test-model')"""
        )

    store = SQLiteStore(db_path)
    assert store.schema_version == 8

    with connect(db_path) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(interaction_logs)")}
        assert "call_site" in columns
        rows = conn.execute(
            "SELECT session_id, call_site FROM interaction_logs ORDER BY id"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["session_id"] == "s-legacy"
        assert rows[0]["call_site"] == "unspecified"


def test_call_site_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotent.db"
    store = SQLiteStore(db_path)
    store.log_interaction("s1", "q1", "a1", call_site="briefing")

    SQLiteStore(db_path)
    SQLiteStore(db_path)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT call_site FROM interaction_logs WHERE session_id='s1'"
        ).fetchall()
        assert [r["call_site"] for r in rows] == ["briefing"]


def test_default_call_site_is_unspecified(tmp_path: Path) -> None:
    db_path = tmp_path / "nexus.db"
    store = SQLiteStore(db_path)
    store.log_interaction("s1", "q1", "a1")

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT call_site FROM interaction_logs WHERE session_id='s1'"
        ).fetchone()
        assert row["call_site"] == "unspecified"
