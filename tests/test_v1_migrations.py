"""V1 schema v13: fresh init and legacy v12 obligation upgrade."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nexus.knowledge.sqlite_store import _LATEST_SCHEMA_VERSION, SQLiteStore, connect

_V13_TABLES = {
    "courses",
    "obligation_sources",
    "calendar_blocks",
    "availability_rules",
    "availability_overrides",
    "state_changes",
    "action_events",
    "effort_samples",
    "academic_state_meta",
}
_V13_OBLIGATION_COLUMNS = {
    "progress_percent",
    "remaining_override_hours",
    "course_id",
    "next_step",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_fresh_store_is_schema_v13(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "nexus.db")
    assert store.schema_version == 13
    assert store.schema_version == _LATEST_SCHEMA_VERSION
    with connect(tmp_path / "nexus.db") as conn:
        tables = _table_names(conn)
        assert _V13_TABLES <= tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(obligations)")}
        assert _V13_OBLIGATION_COLUMNS <= columns
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_obligations_status_due'"
        ).fetchone()


def test_legacy_v12_obligations_upgrade_preserves_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE obligations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                course TEXT,
                kind TEXT NOT NULL,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                source_ids TEXT NOT NULL DEFAULT '[]',
                primary_task_id TEXT,
                estimated_hours REAL,
                spent_hours REAL,
                remaining_hours REAL,
                risk_band TEXT,
                last_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO obligations(
                   id, title, course, kind, due_at, status, source_ids,
                   primary_task_id, estimated_hours, spent_hours, remaining_hours,
                   risk_band, last_seen_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "ob:legacy",
                "Old Homework",
                "CS202_OS",
                "assignment",
                "2026-09-04T23:59:00+00:00",
                "open",
                '["bspace_ical:old"]',
                "bspace_ical:old",
                3.0,
                0.5,
                2.5,
                "unscored",
                "2026-09-01T00:00:00",
                "2026-09-01T00:00:00",
            ),
        )

    store = SQLiteStore(db_path)
    assert store.schema_version == 13
    assert store.schema_version == _LATEST_SCHEMA_VERSION
    row = store.get_obligation("ob:legacy")
    assert row is not None
    assert row["title"] == "Old Homework"
    assert row["due_at"] == "2026-09-04T23:59:00+00:00"
    assert row["status"] == "open"
    assert float(row["estimated_hours"]) == 3.0
    assert float(row["spent_hours"]) == 0.5
    assert float(row["remaining_hours"]) == 2.5
    assert row["risk_band"] == "unscored"
    assert "bspace_ical:old" in row["source_ids"]
    assert "progress_percent" in row
    assert row["progress_percent"] is None
    assert row["remaining_override_hours"] is None
    assert row["course_id"] is None
    assert row["next_step"] is None
    with connect(db_path) as conn:
        tables = _table_names(conn)
        assert _V13_TABLES <= tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(obligations)")}
        assert _V13_OBLIGATION_COLUMNS <= columns
