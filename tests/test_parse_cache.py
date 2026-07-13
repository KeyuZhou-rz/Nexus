from __future__ import annotations

import sqlite3

from nexus.cli.cache import main as cache_main
from nexus.knowledge import parser
from nexus.knowledge.sqlite_store import SQLiteStore


def test_fallback_parse_is_cached(monkeypatch, tmp_path):
    db_path = tmp_path / "nexus.db"
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    calls = 0

    def fake_extract_text(_path):
        nonlocal calls
        calls += 1
        return "A useful topic\n" + ("Detailed explanation. " * 8)

    monkeypatch.setattr(parser, "extract_text", fake_extract_text)
    monkeypatch.setattr("nexus.knowledge.sqlite_store.default_db_path", lambda: db_path)
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    first = parser.parse_document(source, course_id="CS101")
    second = parser.parse_document(source, course_id="CS101")

    assert first.parse_method == "text_fallback"
    assert second.parse_method == "text_fallback_cached"
    assert calls == 1


def test_parser_version_change_invalidates_cache(monkeypatch, tmp_path):
    db_path = tmp_path / "nexus.db"
    source = tmp_path / "notes.txt"
    source.write_text("content", encoding="utf-8")
    calls = 0

    def fake_extract_text(_path):
        nonlocal calls
        calls += 1
        return "Topic\n" + ("Substantial paragraph. " * 8)

    monkeypatch.setattr(parser, "extract_text", fake_extract_text)
    monkeypatch.setattr("nexus.knowledge.sqlite_store.default_db_path", lambda: db_path)
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    parser.parse_document(source, course_id="CS101")
    monkeypatch.setattr(parser, "PARSER_VERSION", "future-version")
    parser.parse_document(source, course_id="CS101")
    assert calls == 2


def test_cache_key_includes_model_and_prompt(tmp_path):
    store = SQLiteStore(tmp_path / "nexus.db")
    chunks = [{"topic": "T", "content": "C"}]
    store.set_parse_cache(
        "hash", "CS101", chunks,
        parser_version="2", model_version="model-a",
        prompt_hash="prompt-a", parse_method="deepseek",
    )
    assert store.get_parse_cache(
        "hash", "CS101", parser_version="2",
        model_version="model-a", prompt_hash="prompt-a",
    ) == chunks
    assert store.get_parse_cache(
        "hash", "CS101", parser_version="2",
        model_version="model-b", prompt_hash="prompt-a",
    ) is None
    assert store.get_parse_cache(
        "hash", "CS101", parser_version="2",
        model_version="model-a", prompt_hash="prompt-b",
    ) is None


def test_legacy_cache_schema_is_migrated_without_reuse(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        conn.execute(
            """CREATE TABLE parse_cache (
                   file_hash TEXT NOT NULL, course_id TEXT NOT NULL DEFAULT '',
                   chunks_json TEXT NOT NULL, file_size INTEGER, created_at TEXT,
                   PRIMARY KEY(file_hash, course_id))"""
        )
        conn.execute(
            "INSERT INTO parse_cache VALUES ('oldhash', 'CS101', '[]', 1, datetime('now'))"
        )

    store = SQLiteStore(db_path)
    assert store.schema_version == 5
    entries = store.list_parse_cache()
    assert entries[0]["parser_version"] == "legacy"
    assert store.get_parse_cache(
        "oldhash", "CS101", parser_version="2",
        model_version="model-a", prompt_hash="prompt-a",
    ) is None


def test_cache_cli_lists_and_clears(tmp_path, capsys):
    db_path = tmp_path / "nexus.db"
    store = SQLiteStore(db_path)
    store.set_parse_cache(
        "hash", "CS101", [{"topic": "T"}],
        parser_version="2", model_version="fallback",
        prompt_hash="prompt", parse_method="text_fallback",
    )
    assert cache_main(["--db-path", str(db_path), "list", "--course", "CS101"]) == 0
    assert "CS101" in capsys.readouterr().out
    assert cache_main(["--db-path", str(db_path), "clear", "--course", "CS101"]) == 0
    assert store.list_parse_cache() == []
