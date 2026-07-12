from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.knowledge.qa_pipeline import _infer_course_from_query
from nexus.knowledge.query_engine import (
    QueryEngine,
    RetrievalThresholds,
    _extract_keywords,
    _merge_and_rerank,
)
from nexus.knowledge.store import ChromaKnowledgeStore, CourseCollectionNotFoundError


DATA_DIR = Path(__file__).with_name("data")
CORPUS = json.loads((DATA_DIR / "retrieval_corpus.json").read_text(encoding="utf-8"))
GOLDEN = json.loads((DATA_DIR / "retrieval_golden.json").read_text(encoding="utf-8"))


class _OfflineCollection:
    def __init__(self, course_id: str, rows: list[dict]) -> None:
        self.name = course_id
        self.metadata = {"course_id": course_id}
        self.rows = rows

    def count(self) -> int:
        return len(self.rows)

    def query(self, *, query_texts, n_results, **_kwargs):
        terms = [term.casefold() for term in _extract_keywords(query_texts[0])]
        ranked = []
        for row in self.rows:
            searchable = f"{row['topic']} {row['keywords']} {row['content']}".casefold()
            matches = sum(term in searchable for term in terms)
            distance = 0.22 - min(matches, 3) * 0.02 if matches else 0.65
            ranked.append((distance, row))
        selected = sorted(ranked, key=lambda item: (item[0], item[1]["id"]))[:n_results]
        return {
            "ids": [[row["id"] for _, row in selected]],
            "documents": [[row["content"] for _, row in selected]],
            "metadatas": [[row for _, row in selected]],
            "distances": [[distance for distance, _ in selected]],
        }

    def get(self, *, ids=None, **_kwargs):
        rows = self.rows if ids is None else [row for row in self.rows if row["id"] in ids]
        return {
            "ids": [row["id"] for row in rows],
            "documents": [row["content"] for row in rows],
            "metadatas": [row for row in rows],
        }


class _OfflineClient:
    def __init__(self) -> None:
        courses = sorted({row["course_id"] for row in CORPUS})
        self.collections = {
            course: _OfflineCollection(
                course, [row for row in CORPUS if row["course_id"] == course]
            )
            for course in courses
        }

    def list_collections(self):
        return list(self.collections.values())

    def get_collection(self, name, **_kwargs):
        if name not in self.collections:
            raise ValueError(f"unknown collection: {name}")
        return self.collections[name]


@pytest.fixture(scope="module")
def engine() -> QueryEngine:
    store = ChromaKnowledgeStore.__new__(ChromaKnowledgeStore)
    store._client = _OfflineClient()
    store._default_ef = object()
    store._per_course = True
    store.collection = None
    query_engine = QueryEngine.__new__(QueryEngine)
    query_engine._chroma = store
    query_engine.use_llm_expansion = False
    query_engine.n_semantic = 5
    query_engine.n_keyword = 5
    query_engine.top_k = 5
    query_engine.thresholds = RetrievalThresholds()
    return query_engine


@pytest.mark.parametrize("case", GOLDEN, ids=lambda case: case["query"])
def test_offline_golden_retrieval(case: dict, engine: QueryEngine) -> None:
    if case.get("expected_error"):
        with pytest.raises(CourseCollectionNotFoundError):
            engine.retrieve(
                case["query"], course_id=case["course_id"], expanded_queries=[case["query"]]
            )
        return

    result = engine.retrieve(
        case["query"], course_id=case["course_id"], expanded_queries=[case["query"]]
    )
    actual = result.chunks[0].chunk_id if result.chunks else None
    assert actual == case.get("expected")


def test_golden_fixture_has_twenty_cases_and_required_coverage() -> None:
    assert len(GOLDEN) == 20
    assert sum(case.get("expected") is None and not case.get("expected_error") for case in GOLDEN) >= 4
    assert any(case.get("expected_error") for case in GOLDEN)
    assert sum(any("\u4e00" <= char <= "\u9fff" for char in case["query"]) for case in GOLDEN) >= 10


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("those examples are useful", None),
        ("diagnose this issue", None),
        ("mysql tuning", None),
        ("CS202_OSextra process", None),
        ("OS 里的进程", "CS202_OS"),
        ("操作系统中的死锁", "CS202_OS"),
        ("SQL index", "CS305_DB"),
        ("CS310_NET TCP", "CS310_NET"),
    ],
)
def test_course_inference_uses_token_boundaries(query: str, expected: str | None) -> None:
    known = ["CS202_OS", "CS305_DB", "CS310_NET", "GHOST"]
    assert _infer_course_from_query(query, known) == expected


def test_course_keyword_without_known_collection_falls_back_to_global() -> None:
    assert _infer_course_from_query("operating systems process", []) is None


def test_path_specific_thresholds_are_configurable() -> None:
    semantic = [("chunk", "text", {}, 0.30)]
    assert _merge_and_rerank(semantic, []) == []
    relaxed = RetrievalThresholds(semantic_only=0.31)
    assert _merge_and_rerank(semantic, [], thresholds=relaxed)[0].chunk_id == "chunk"
    with pytest.raises(ValueError, match="semantic_only"):
        RetrievalThresholds(semantic_only=-0.1)
