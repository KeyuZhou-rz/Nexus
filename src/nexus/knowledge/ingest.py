from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .document_text import extract_text
from .ingestion_service import TextDocument, ingest_text_documents
from .store import ChromaKnowledgeStore


@dataclass
class IngestSummary:
    files: int
    chunks: int


def ingest_files(
    paths: list[Path],
    store: ChromaKnowledgeStore,
    *,
    course_id: str,
    doc_type: str,
    timestamp: str | None = None,
    max_chars: int = 900,
    overlap: int = 120,
    id_namespace: str = "chunk",
) -> IngestSummary:
    """Ingest supported document files into Chroma with metadata."""
    now = timestamp or datetime.now().date().isoformat()
    documents: list[TextDocument] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        text = extract_text(path)
        if not text.strip():
            continue
        documents.append(
            TextDocument(
                source_path=path,
                text=text,
                course_id=course_id,
                doc_type=doc_type,
                timestamp=now,
            )
        )

    stats = ingest_text_documents(
        documents,
        store,
        max_chars=max_chars,
        overlap=overlap,
        id_namespace=id_namespace,
    )
    return IngestSummary(files=stats.files, chunks=stats.chunks)


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
    """Backward-compatible ingest for markdown/text paths."""
    text_paths = [p for p in paths if p.suffix.lower() in {".md", ".txt", ".markdown"}]
    return ingest_files(
        text_paths,
        store,
        course_id=course_id,
        doc_type=doc_type,
        timestamp=timestamp,
        max_chars=max_chars,
        overlap=overlap,
        id_namespace="chunk",
    )
