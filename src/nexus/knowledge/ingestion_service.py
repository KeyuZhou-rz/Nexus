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


def document_id(course_id: str, source_file: str) -> str:
    """Canonical document identity shared by every ingestion path."""
    return f"{course_id}/{Path(source_file).name}"


def content_hash(content: bytes | str) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(payload).hexdigest()


def file_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def chunk_id(course_id: str, file_hash: str, idx: int, namespace: str = "chunk") -> str:
    """Canonical, retry-stable chunk identity based on course and file content."""
    safe_course = "".join(c if c.isalnum() or c in "_-" else "_" for c in course_id)
    return f"{namespace}:{safe_course}:{file_hash[:20]}:{idx:04d}"


def _build_metadata(
    document: TextDocument,
    chunk_index: int,
    file_hash: str,
    chunk_identifier: str,
) -> dict[str, str | int]:
    source_file = document.source_path.name
    metadata: dict[str, str | int] = {
        "schema_version": "1",
        "document_id": document_id(document.course_id, source_file),
        "chunk_id": chunk_identifier,
        "source_file": source_file,
        "file_name": source_file,
        "doc_type": document.doc_type,
        "type": document.doc_type,
        "timestamp": document.timestamp,
        "course_id": document.course_id,
        "chunk_index": chunk_index,
        "file_hash": file_hash,
        "source_path": document.source_path_meta or str(document.source_path),
        "session_id": document.session_id or "",
    }
    collisions = set(metadata).intersection(document.extra_metadata)
    if collisions:
        raise ValueError(
            "extra_metadata cannot override canonical fields: "
            + ", ".join(sorted(collisions))
        )
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

        file_hash = content_hash(content)
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, str | int]] = []
        for idx, chunk in enumerate(chunks):
            identifier = chunk_id(document.course_id, file_hash, idx, namespace=id_namespace)
            ids.append(identifier)
            docs.append(chunk)
            metas.append(_build_metadata(document, idx, file_hash, identifier))

        # Legacy stores use a fixed collection; course identity remains explicit
        # in the canonical metadata contract.
        store.upsert_chunks(ids, docs, metas)
        stats.files += 1
        stats.chunks += len(chunks)

    return stats
