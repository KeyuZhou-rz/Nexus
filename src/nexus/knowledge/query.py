from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import ChromaKnowledgeStore


@dataclass
class QueryItem:
    text: str
    metadata: dict[str, Any]
    distance: float | None


@dataclass
class QuerySummary:
    items: list[QueryItem]


def query_knowledge(
    db_dir: Path,
    query_text: str,
    *,
    n_results: int = 5,
    course_id: str | None = None,
    doc_type: str | None = None,
) -> QuerySummary:
    """Query knowledge chunks with optional metadata filtering."""
    store = ChromaKnowledgeStore(db_dir)

    where: dict[str, Any] | None = None
    filters: list[dict[str, str]] = []
    if course_id:
        filters.append({"course_id": course_id})
    if doc_type:
        filters.append({"doc_type": doc_type})

    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}

    result = store.query(query_text, n_results=n_results, where=where)

    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]

    items: list[QueryItem] = []
    for idx, text in enumerate(docs):
        metadata = metas[idx] if idx < len(metas) else {}
        distance = dists[idx] if idx < len(dists) else None
        items.append(QueryItem(text=text, metadata=metadata or {}, distance=distance))

    return QuerySummary(items=items)
