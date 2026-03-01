from __future__ import annotations

import hashlib
import html
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.knowledge.chunking import chunk_text
from nexus.knowledge.store import ChromaKnowledgeStore
from nexus.memory_extractor import WeakPointCandidate
from nexus.memory_update import merge_candidates_into_state
from nexus.state_store import load_state, save_state

_DOC_PATTERNS = [
    re.compile(r"(?:common mistakes?|frequent errors?|pitfalls?|don'?t\s+confuse)\s*[:\-]\s*(?P<topic>[^.\n]{3,80})", re.IGNORECASE),
    re.compile(r"(?:易错点|常见错误|薄弱点|注意|不要混淆)\s*[:：\-]\s*(?P<topic>[\u4e00-\u9fffA-Za-z0-9\-\s]{2,60})"),
]
_TOPIC_CLEAN = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9\-\s]")


def _normalize_topic(raw: str) -> str:
    text = _TOPIC_CLEAN.sub(" ", raw or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _extract_text_from_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.DOTALL)
    return html.unescape("\n".join(part.strip() for part in parts if part.strip()))


def _extract_text_from_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # optional
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path))
        texts = [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        return ""
    return "\n".join(t.strip() for t in texts if t.strip())


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".docx":
        return _extract_text_from_docx(path)
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    return ""


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


def _chunk_id(source_path: Path, idx: int, text: str) -> str:
    digest = hashlib.sha1(f"{source_path}:{idx}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"archive_chunk:{digest}"


def run_archive_post_ingest(
    *,
    archives: list[dict[str, Any]],
    data_dir: Path,
    db_dir: Path,
    collection_name: str = "nexus_chunks",
    max_chars: int = 900,
    overlap: int = 120,
    store: ChromaKnowledgeStore | None = None,
) -> dict[str, Any]:
    """Ingest archived files into knowledge DB and extract weak points into learner state."""
    files_scanned = 0
    files_ingested = 0
    total_chunks = 0
    failures: list[dict[str, str]] = []
    candidates: list[WeakPointCandidate] = []
    now_iso = datetime.now().astimezone().isoformat()
    now_date = datetime.now().date().isoformat()

    if store is None:
        store = ChromaKnowledgeStore(db_dir, collection_name=collection_name)

    for item in archives:
        archived_path = Path(str(item.get("archived_path", "")).strip())
        if not archived_path.exists() or not archived_path.is_file():
            continue
        files_scanned += 1
        try:
            text = _extract_text(archived_path)
            if not text.strip():
                continue

            chunks = chunk_text(text, max_chars=max_chars, overlap=overlap)
            if not chunks:
                continue

            course = str(item.get("course", "brightspace")).strip() or "brightspace"
            ids: list[str] = []
            docs: list[str] = []
            metas: list[dict[str, str]] = []
            for idx, chunk in enumerate(chunks):
                ids.append(_chunk_id(archived_path, idx, chunk))
                docs.append(chunk)
                metas.append(
                    {
                        "file_name": archived_path.name,
                        "doc_type": "archive_attachment",
                        "timestamp": now_date,
                        "course_id": course,
                        "chunk_index": str(idx),
                        "source_path": str(archived_path),
                    }
                )

            store.upsert_chunks(ids, docs, metas)
            files_ingested += 1
            total_chunks += len(chunks)
            candidates.extend(_weak_candidates_from_text(text, evidence_id=archived_path.name))
        except Exception as exc:
            failures.append({"path": str(archived_path), "error": str(exc)})

    state_path = data_dir / "state.json"
    state = load_state(state_path)
    state = merge_candidates_into_state(state, candidates, now_iso=now_iso)
    save_state(state_path, state)

    summary = {
        "status": "success",
        "files_scanned": files_scanned,
        "files_ingested": files_ingested,
        "chunks": total_chunks,
        "weak_candidates": len(candidates),
        "active_weak_points": len(state.weak_points),
        "state_path": str(state_path.resolve()),
        "db_dir": str(db_dir.resolve()),
        "failures": failures,
    }
    return summary
