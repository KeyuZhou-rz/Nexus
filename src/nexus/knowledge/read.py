from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import ChromaKnowledgeStore


@dataclass
class ReadItem:
    text: str
    metadata: dict[str, Any]


@dataclass
class ReadSummary:
    items: list[ReadItem]


def _chunk_index(metadata: dict[str, Any]) -> int:
    try:
        return int(str(metadata.get("chunk_index", "0")))
    except ValueError:
        return 0


def read_chunks(
    db_dir: Path,
    *,
    limit: int = 20,
    course_id: str | None = None,
    doc_type: str | None = None,
    source_path: str | None = None,
    session_id: str | None = None,
    collection_name: str = "nexus_chunks",
) -> ReadSummary:
    """Read stored chunks directly for human review/debug."""
    store = ChromaKnowledgeStore(db_dir, collection_name=collection_name)

    filters: list[dict[str, str]] = []
    if course_id:
        filters.append({"course_id": course_id})
    if doc_type:
        filters.append({"doc_type": doc_type})
    if source_path:
        filters.append({"source_path": source_path})
    if session_id:
        filters.append({"session_id": session_id})

    where: dict[str, Any] | None = None
    if len(filters) == 1:
        where = filters[0]
    elif len(filters) > 1:
        where = {"$and": filters}

    result = store.fetch_chunks(where=where, limit=limit)
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []

    items: list[ReadItem] = []
    for idx, text in enumerate(docs):
        metadata = metas[idx] if idx < len(metas) else {}
        items.append(ReadItem(text=str(text), metadata=metadata or {}))

    items.sort(key=lambda item: (str(item.metadata.get("file_name", "")), _chunk_index(item.metadata)))
    return ReadSummary(items=items)
