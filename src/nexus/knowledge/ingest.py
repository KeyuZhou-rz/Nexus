from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .chunking import chunk_text
from .store import ChromaKnowledgeStore


@dataclass
class IngestSummary:
    files: int
    chunks: int


def _chunk_id(file_path: Path, idx: int, text: str) -> str:
    digest = hashlib.sha1(f"{file_path}:{idx}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"chunk:{digest}"


def ingest_markdown_files(
    paths: list[Path],
    store: ChromaKnowledgeStore,
    *,
    course_id: str,
    doc_type: str,
    timestamp: str | None = None,
    max_chars: int = 900,
    overlap: int = 120,
) -> IngestSummary:
    """Ingest markdown/text files into Chroma with metadata."""
    now = timestamp or datetime.now().date().isoformat()
    total_chunks = 0
    file_count = 0

    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        chunks = chunk_text(text, max_chars=max_chars, overlap=overlap)
        if not chunks:
            continue

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, str]] = []
        for idx, chunk in enumerate(chunks):
            ids.append(_chunk_id(path, idx, chunk))
            docs.append(chunk)
            metas.append(
                {
                    "file_name": path.name,
                    "doc_type": doc_type,
                    "timestamp": now,
                    "course_id": course_id,
                    "chunk_index": str(idx),
                }
            )

        store.upsert_chunks(ids, docs, metas)
        total_chunks += len(chunks)
        file_count += 1

    return IngestSummary(files=file_count, chunks=total_chunks)
