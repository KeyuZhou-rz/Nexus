from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from nexus.knowledge.sqlite_store import SQLiteStore, connect


_INIT_SCRIPT = """
import sys
import time
from pathlib import Path
from nexus.knowledge.sqlite_store import SQLiteStore

db_path, ready_path, gate_path = map(Path, sys.argv[1:])
ready_path.touch()
deadline = time.time() + 15
while not gate_path.exists():
    if time.time() > deadline:
        raise TimeoutError("test gate was not released")
    time.sleep(0.005)
SQLiteStore(db_path)
"""


def _initialize_in_two_processes(db_path: Path, tmp_path: Path) -> None:
    gate = tmp_path / "start-gate"
    processes: list[subprocess.Popen[str]] = []
    ready_paths: list[Path] = []
    for index in range(2):
        ready = tmp_path / f"ready-{index}"
        ready_paths.append(ready)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _INIT_SCRIPT,
                    str(db_path),
                    str(ready),
                    str(gate),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )

    deadline = time.time() + 15
    while not all(path.exists() for path in ready_paths):
        if time.time() > deadline:
            raise TimeoutError("child processes did not reach migration barrier")
        time.sleep(0.005)
    gate.touch()

    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        if process.returncode:
            failures.append((process.returncode, stdout, stderr))
    assert failures == []


def test_two_processes_can_initialize_new_database(tmp_path: Path) -> None:
    db_path = tmp_path / "new.db"

    _initialize_in_two_processes(db_path, tmp_path)

    store = SQLiteStore(db_path)
    assert store.schema_version == 7
    with connect(db_path) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
        assert [row[0] for row in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )] == [1, 2, 3, 4, 5, 6, 7]


def test_two_processes_can_migrate_legacy_database(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        conn.execute(
            """CREATE TABLE parse_cache (
                   file_hash TEXT NOT NULL,
                   course_id TEXT NOT NULL DEFAULT '',
                   chunks_json TEXT NOT NULL,
                   file_size INTEGER,
                   created_at TEXT,
                   PRIMARY KEY (file_hash, course_id)
               )"""
        )
        conn.execute(
            """INSERT INTO parse_cache
                   (file_hash, course_id, chunks_json, file_size, created_at)
               VALUES ('hash', 'OS', '[]', 12, '2026-01-01')"""
        )

    _initialize_in_two_processes(db_path, tmp_path)

    store = SQLiteStore(db_path)
    assert store.schema_version == 7
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT parser_version, model_version, prompt_hash, chunks_json
               FROM parse_cache WHERE file_hash='hash'"""
        ).fetchone()
        assert tuple(row) == ("legacy", "legacy", "legacy", "[]")


def test_recorded_partial_schema_is_repaired(tmp_path: Path) -> None:
    db_path = tmp_path / "partial.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO schema_migrations(version) VALUES (?)", [(1,), (2,), (3,)]
        )
        conn.execute(
            """CREATE TABLE parse_cache (
                   file_hash TEXT NOT NULL,
                   course_id TEXT NOT NULL,
                   parser_version TEXT,
                   chunks_json TEXT NOT NULL,
                   created_at TEXT,
                   PRIMARY KEY (file_hash, course_id)
               )"""
        )
        conn.execute(
            """INSERT INTO parse_cache
                   (file_hash, course_id, parser_version, chunks_json, created_at)
               VALUES ('partial', 'OS', 'parser-v1', '[{}]', '2026-01-01')"""
        )
        conn.execute(
            """CREATE TABLE learning_events (
                   id INTEGER PRIMARY KEY,
                   concept_id TEXT NOT NULL,
                   event_type TEXT NOT NULL,
                   session_id TEXT,
                   context TEXT,
                   timestamp TIMESTAMP,
                   evidence_type TEXT
               )"""
        )

    SQLiteStore(db_path)

    with connect(db_path) as conn:
        cache_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(parse_cache)")
        }
        assert {
            "parser_version", "model_version", "prompt_hash", "parse_method"
        } <= cache_columns
        row = conn.execute(
            """SELECT parser_version, model_version, prompt_hash, parse_method,
                      chunks_json
               FROM parse_cache WHERE file_hash='partial'"""
        ).fetchone()
        assert tuple(row) == (
            "parser-v1", "legacy", "legacy", "deepseek", "[{}]"
        )
        event_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(learning_events)")
        }
        assert {"evidence_type", "trust_status", "classified_at"} <= event_columns
