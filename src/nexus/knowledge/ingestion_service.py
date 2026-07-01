from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import chunk_text
from .store import ChromaKnowledgeStore


@dataclass
class TextDocument:
    """Normalized text document payload for chunk ingestion."""

    source_path: Path
    text: str
    course_id: str
    doc_type: str
    timestamp: str
    source_path_meta: str | None = None
    session_id: str | None = None
    extra_metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class IngestStats:
    files: int = 0
    chunks: int = 0


def _chunk_id(source_path: Path, idx: int, text: str, namespace: str) -> str:
    digest = hashlib.sha1(f"{source_path}:{idx}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"{namespace}:{digest}"


def _build_metadata(document: TextDocument, chunk_index: int) -> dict[str, str]:
    metadata = {
        "file_name": document.source_path.name,
        "doc_type": document.doc_type,
        "timestamp": document.timestamp,
        "course_id": document.course_id,
        "chunk_index": str(chunk_index),
        # Keep schema stable across ingestion paths for easier debugging/filtering.
        "source_path": document.source_path_meta or str(document.source_path),
        "session_id": document.session_id or "",
    }
    metadata.update(document.extra_metadata)
    return metadata


def ingest_text_documents(
    documents: list[TextDocument],
    store: ChromaKnowledgeStore,
    *,
    max_chars: int = 900,
    overlap: int = 120,
    id_namespace: str = "chunk",
) -> IngestStats:
    """Chunk and upsert normalized text documents into Chroma."""
    stats = IngestStats()

    for document in documents:
        content = (document.text or "").strip()
        if not content:
            continue

        chunks = chunk_text(content, max_chars=max_chars, overlap=overlap)
        if not chunks:
            continue

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, str]] = []
        for idx, chunk in enumerate(chunks):
            ids.append(_chunk_id(document.source_path, idx, chunk, namespace=id_namespace))
            docs.append(chunk)
            metas.append(_build_metadata(document, idx))

        store.upsert_chunks(ids, docs, metas)
        stats.files += 1
        stats.chunks += len(chunks)

    return stats
