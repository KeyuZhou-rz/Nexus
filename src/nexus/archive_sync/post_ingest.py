from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.knowledge.document_text import extract_text
from nexus.knowledge.ingestion_service import TextDocument, ingest_text_documents
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.knowledge.concept_resolver import ConceptResolver
from nexus.knowledge.user_profile import UserProfileManager
from nexus.knowledge.temporal_memory import TemporalMemory
from nexus.knowledge.store import ChromaKnowledgeStore
from nexus.memory_extractor import WeakPointCandidate
from nexus.memory_writer import record_weak_point

_DOC_PATTERNS = [
    re.compile(r"(?:common mistakes?|frequent errors?|pitfalls?|don'?t\s+confuse)\s*[:\-]\s*(?P<topic>[^.\n]{3,80})", re.IGNORECASE),
    re.compile(r"(?:易错点|常见错误|薄弱点|注意|不要混淆)\s*[:：\-]\s*(?P<topic>[一-鿿A-Za-z0-9\-\s]{2,60})"),
]
_TOPIC_CLEAN = re.compile(r"[^一-鿿A-Za-z0-9\-\s]")


def _normalize_topic(raw: str) -> str:
    text = _TOPIC_CLEAN.sub(" ", raw or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _weak_candidates_from_text(text: str, evidence_id: str) -> list[WeakPointCandidate]:
    candidates: dict[str, WeakPointCandidate] = {}
    for line in text.splitlines():
        content = line.strip()
        if len(content) < 4:
            continue
        for pattern in _DOC_PATTERNS:
            m = pattern.search(content)
            if not m:
                continue
            topic = _normalize_topic(m.groupdict().get("topic") or "")
            if len(topic) < 2:
                continue
            candidates[topic] = WeakPointCandidate(
                topic=topic,
                confidence=0.65,
                evidence=content[:220],
                evidence_msg_ids=[evidence_id],
            )
    return list(candidates.values())


def run_archive_post_ingest(
    *,
    archives: list[dict[str, Any]],
    data_dir: Path,
    db_dir: Path,
    sqlite_path: Path | None = None,
    collection_name: str = "nexus_chunks",
    max_chars: int = 900,
    overlap: int = 120,
    store: ChromaKnowledgeStore | None = None,
) -> dict[str, Any]:
    """Ingest archived files into knowledge DB and extract weak points into SQLite profile."""
    files_scanned = 0
    files_ingested = 0
    total_chunks = 0
    failures: list[dict[str, str]] = []
    candidates: list[WeakPointCandidate] = []
    now_date = datetime.now().date().isoformat()

    if store is None:
        store = ChromaKnowledgeStore(db_dir, collection_name=collection_name)

    for item in archives:
        archived_path = Path(str(item.get("archived_path", "")).strip())
        if not archived_path.exists() or not archived_path.is_file():
            continue
        files_scanned += 1
        try:
            text = extract_text(archived_path)
            if not text.strip():
                continue

            course = str(item.get("course", "brightspace")).strip() or "brightspace"
            stats = ingest_text_documents(
                [
                    TextDocument(
                        source_path=archived_path,
                        text=text,
                        course_id=course,
                        doc_type="archive_attachment",
                        timestamp=now_date,
                        source_path_meta=str(archived_path),
                    )
                ],
                store,
                max_chars=max_chars,
                overlap=overlap,
                id_namespace="archive_chunk",
            )
            if stats.files == 0:
                continue

            files_ingested += stats.files
            total_chunks += stats.chunks
            candidates.extend(_weak_candidates_from_text(text, evidence_id=archived_path.name))
        except Exception as exc:
            failures.append({"path": str(archived_path), "error": str(exc)})

    # Unified SQLite write via memory_writer
    resolved_count = 0
    if sqlite_path is not None and candidates:
        sqlite_store = SQLiteStore(sqlite_path)
        resolver = ConceptResolver(sqlite_store)
        profile_mgr = UserProfileManager(sqlite_store)
        temporal = TemporalMemory(sqlite_store)

        for candidate in candidates:
            # Use the course from the archive item (carried via evidence_id pattern)
            # Default to "brightspace" — course context may be limited here
            result = record_weak_point(
                resolver, profile_mgr, temporal,
                candidate.topic, course_id="brightspace",
                context={"source": "post_ingest"},
            )
            if result["resolved"]:
                resolved_count += 1

        profile = profile_mgr.get()
        active_weak = len(profile.get("weak_points", []))
    else:
        active_weak = 0

    summary = {
        "status": "success",
        "files_scanned": files_scanned,
        "files_ingested": files_ingested,
        "chunks": total_chunks,
        "weak_candidates": len(candidates),
        "active_weak_points": active_weak,
        "resolved_to_concepts": resolved_count,
        "db_dir": str(db_dir.resolve()),
        "failures": failures,
    }
    return summary
