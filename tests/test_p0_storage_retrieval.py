from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus.knowledge.query_engine import QueryEngine, RetrievalError
from nexus.knowledge.sqlite_store import SQLiteStore, connect
from nexus.knowledge.store import (
    ChromaKnowledgeStore,
    CourseCollectionNotFoundError,
)


def test_connect_closes_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "close.db"

    with connect(db_path) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER)")

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")


def test_schema_creates_query_path_indexes(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "indexes.db")
    expected = {
        "idx_logs_session_time",
        "idx_logs_course_time",
        "idx_learning_events_concept_time",
        "idx_query_log_concept_time",
        "idx_checkpoint_concepts_importance",
    }

    with connect(store.db_path) as conn:
        actual = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert expected <= actual


class _Collection:
    def __init__(self, course_id: str, rows: list[tuple[str, str, float]]) -> None:
        self.metadata = {"course_id": course_id}
        self.rows = rows

    def query(self, **_kwargs):
        return {
            "ids": [[row[0] for row in self.rows]],
            "documents": [[row[1] for row in self.rows]],
            "metadatas": [[{"course_id": self.metadata["course_id"]} for _ in self.rows]],
            "distances": [[row[2] for row in self.rows]],
        }


class _Client:
    def __init__(self, collections: dict[str, _Collection]) -> None:
        self.collections = collections
        self.created: list[str] = []

    def list_collections(self):
        return list(self.collections.values())

    def get_collection(self, name: str, **_kwargs):
        if name not in self.collections:
            raise ValueError("missing")
        return self.collections[name]

    def get_or_create_collection(self, name: str, **_kwargs):
        self.created.append(name)
        return self.collections.setdefault(name, _Collection(name, []))


def _store(client: _Client) -> ChromaKnowledgeStore:
    store = ChromaKnowledgeStore.__new__(ChromaKnowledgeStore)
    store._client = client
    store._default_ef = object()
    store._per_course = True
    store.collection = None
    return store


def test_global_query_merges_existing_courses_by_distance() -> None:
    client = _Client({
        "COURSE_A": _Collection("COURSE_A", [("a", "A", 0.4)]),
        "COURSE_B": _Collection("COURSE_B", [("b", "B", 0.1)]),
    })

    result = _store(client).query("topic", n_results=2, course_id=None)

    assert result["ids"] == [["b", "a"]]
    assert client.created == []


def test_unknown_course_query_does_not_create_collection() -> None:
    client = _Client({})

    with pytest.raises(CourseCollectionNotFoundError):
        _store(client).query("topic", course_id="UNKNOWN")

    assert client.created == []


class _FailingChroma:
    def query(self, *_args, **_kwargs):
        raise OSError("backend unavailable")


def test_query_engine_surfaces_total_semantic_failure() -> None:
    engine = QueryEngine.__new__(QueryEngine)
    engine._chroma = _FailingChroma()
    engine.n_semantic = 5
    engine.n_keyword = 5
    engine.top_k = 5

    with pytest.raises(RetrievalError, match="backend unavailable"):
        engine.retrieve("process", expanded_queries=["process"])
