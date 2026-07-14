"""
ChromaDB → SQLite Chunks 回填引擎 — Nexus Phase M1-2

从 ChromaDB 集合读取已有 vector chunks，将其结构化元数据写入
SQLite chunks 表，同时更新 documents 表的 chunk_count。

幂等：INSERT OR REPLACE 保证重复执行安全。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

import chromadb

from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsistencyIssue:
    code: str
    message: str
    course_id: str | None = None
    chunk_id: str | None = None
    document_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "message": self.message,
                "course_id": self.course_id,
                "chunk_id": self.chunk_id,
                "document_id": self.document_id,
            }.items()
            if value is not None
        }


# ChromaDB metadata fields expected on every vector chunk
_CANONICAL_CHROMA_FIELDS = [
    "schema_version", "document_id", "chunk_id",
    "file_hash", "chunk_index",
]


class KnowledgeConsistencyAudit:
    """Read-only SQLite/Chroma cross-store audit.

    The auditor never instantiates ``SQLiteStore`` (which may migrate a DB) and
    only uses Chroma ``get``/``list_collections`` calls.

    Checks (14 rules):
      1. chroma_chunk_missing_sqlite
      2. course_id_mismatch
      3. missing_source_file
      4. chroma_document_missing_sqlite
      5. sqlite_chunk_missing_chroma
      6. document_chunk_count_mismatch
      7. chroma_metadata_missing_field
      8. collection_missing_course_metadata
      9. sqlite_chunks_missing_chunk_index_column
      10. document_non_sha256_hash
      11. chroma_pagination_gap
      12. collection_course_id_mismatch
    """

    def __init__(self, sqlite_path: Path, chroma_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self.chroma_path = chroma_path

    def run(self) -> dict[str, Any]:
        issues: list[ConsistencyIssue] = []
        try:
            sqlite_data = self._read_sqlite()
        except (OSError, sqlite3.Error) as exc:
            return {"ok": False, "issues": [{"code": "sqlite_unreadable", "message": str(exc)}]}

        documents, chunks, has_chunk_index = sqlite_data

        # ── SQLite-side structural checks ────────────────────────────
        if not has_chunk_index:
            issues.append(ConsistencyIssue(
                "sqlite_chunks_missing_chunk_index_column",
                "chunks table is missing chunk_index column",
            ))

        # 4B-1: document hash audit (SHA-256 = 64 hex chars)
        for doc_id, doc in documents.items():
            fh = doc["file_hash"] or ""
            if len(fh) == 32:
                issues.append(ConsistencyIssue(
                    "document_non_sha256_hash",
                    f"document file_hash is MD5 (32 chars), expected SHA-256 (64 chars)",
                    doc["course_id"], document_id=doc_id,
                ))
            elif len(fh) not in (64, 0) and fh != "backfill":
                issues.append(ConsistencyIssue(
                    "document_non_sha256_hash",
                    f"document file_hash has unexpected length {len(fh)}",
                    doc["course_id"], document_id=doc_id,
                ))

        sqlite_ids = set(chunks)
        chroma_ids: set[str] = set()
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self.chroma_path))
            collections = client.list_collections()
        except Exception as exc:
            return {"ok": False, "issues": [{"code": "chroma_unreadable", "message": str(exc)}]}

        # ── Per-collection checks ─────────────────────────────────────
        for listed in collections:
            collection = listed
            name = getattr(listed, "name", str(listed))
            if not hasattr(collection, "get"):
                collection = client.get_collection(name)

            # 4B-1: collection metadata audit
            coll_meta = getattr(collection, "metadata", None) or {}
            coll_course = coll_meta.get("course_id")
            if not coll_course:
                issues.append(ConsistencyIssue(
                    "collection_missing_course_metadata",
                    f"collection '{name}' has no course_id in metadata",
                    course_id=name,
                ))
            elif coll_course != name:
                issues.append(ConsistencyIssue(
                    "collection_course_id_mismatch",
                    f"collection '{name}' metadata.course_id='{coll_course}'",
                    course_id=name,
                ))

            # 4B-1: pagination audit
            try:
                declared_count = collection.count()
            except Exception:
                declared_count = -1

            result = collection.get(limit=100_000, include=["metadatas"])
            ids = result.get("ids", []) or []
            metadatas = result.get("metadatas", []) or []

            if declared_count >= 0 and len(ids) != declared_count:
                issues.append(ConsistencyIssue(
                    "chroma_pagination_gap",
                    f"collection '{name}': count()={declared_count}, get() returned {len(ids)}",
                    course_id=name,
                ))

            collection_course = coll_course or name

            # ── Per-chunk checks ────────────────────────────────────
            for chunk_id, metadata in zip(ids, metadatas):
                chunk_id = str(chunk_id)
                chroma_ids.add(chunk_id)
                meta = metadata or {}
                course_id = str(meta.get("course_id") or collection_course)

                # 4B-1: metadata completeness per canonical field
                for field in _CANONICAL_CHROMA_FIELDS:
                    val = meta.get(field)
                    if val is None or (isinstance(val, str) and not val.strip()) or val == 0:
                        issues.append(ConsistencyIssue(
                            "chroma_metadata_missing_field",
                            f"Chroma metadata missing field '{field}'",
                            course_id, chunk_id,
                        ))

                if chunk_id not in chunks:
                    issues.append(ConsistencyIssue(
                        "chroma_chunk_missing_sqlite",
                        "Chroma chunk has no SQLite row", course_id, chunk_id,
                    ))
                    continue
                row = chunks[chunk_id]
                if row["course_id"] != course_id:
                    issues.append(ConsistencyIssue(
                        "course_id_mismatch",
                        "Chroma and SQLite course_id differ", course_id, chunk_id,
                    ))
                source_file = meta.get("source_file") or meta.get("file_name")
                expected_doc = f"{course_id}/{source_file}" if source_file else None
                if not source_file:
                    issues.append(ConsistencyIssue(
                        "missing_source_file",
                        "Chroma metadata has no source_file", course_id, chunk_id,
                    ))
                elif expected_doc not in documents:
                    issues.append(ConsistencyIssue(
                        "chroma_document_missing_sqlite",
                        "Chroma source document has no SQLite row",
                        course_id, chunk_id, expected_doc,
                    ))

        for chunk_id, row in chunks.items():
            if chunk_id not in chroma_ids:
                issues.append(ConsistencyIssue(
                    "sqlite_chunk_missing_chroma",
                    "SQLite chunk has no Chroma vector",
                    row["course_id"], chunk_id, row["document_id"],
                ))
        for doc_id, doc in documents.items():
            actual = sum(1 for row in chunks.values() if row["document_id"] == doc_id)
            if int(doc["chunk_count"] or 0) != actual:
                issues.append(ConsistencyIssue(
                    "document_chunk_count_mismatch",
                    f"document declares {doc['chunk_count']} chunks, found {actual}",
                    doc["course_id"], document_id=doc_id,
                ))

        # Build per-issue-code summary for audit reports
        issue_counts: dict[str, int] = {}
        for issue in issues:
            issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1

        return {
            "ok": not issues,
            "summary": {
                "documents": len(documents),
                "sqlite_chunks": len(chunks),
                "chroma_chunks": len(chroma_ids),
                "issues": len(issues),
                "issue_counts": issue_counts,
            },
            "issues": [issue.as_dict() for issue in issues],
        }

    def _read_sqlite(self) -> tuple[dict[str, sqlite3.Row], dict[str, sqlite3.Row], bool]:
        uri = f"file:{quote(str(self.sqlite_path.absolute()))}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            documents = {row["id"]: row for row in conn.execute("SELECT * FROM documents")}
            chunks = {row["chunk_id"]: row for row in conn.execute("SELECT * FROM chunks")}
            # Check if chunk_index column exists
            cols = conn.execute("PRAGMA table_info('chunks')").fetchall()
            has_chunk_index = any(col["name"] == "chunk_index" for col in cols)
        return documents, chunks, has_chunk_index


def _split_csv(value: Any) -> list[str]:
    """将逗号分隔字符串（或已是 list）拆为去空 list。"""
    if not value:
        return []
    if isinstance(value, list):
        return [v.strip() for v in value if v and str(v).strip()]
    parts = [p.strip() for p in str(value).split(",")]
    return [p for p in parts if p]


class ChunkBackfill:
    """
    从 ChromaDB 回填 chunks 到 SQLite。

    Usage:
        store = SQLiteStore(Path("data/nexus.db"))
        backfill = ChunkBackfill(Path("data/chroma"), store)
        stats = backfill.run()
        print(stats)  # {"CS202_OS": {"chunks": 70, "docs": 5}}
    """

    def __init__(self, chroma_path: Path, store: SQLiteStore) -> None:
        self._chroma_path = chroma_path
        self._store = store
        self._client: chromadb.PersistentClient | None = None

    def close(self) -> None:
        """Release the lazy Chroma client when a backfill run is complete."""
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def __del__(self) -> None:  # pragma: no cover - interpreter cleanup fallback
        self.close()

    @property
    def client(self) -> chromadb.PersistentClient:
        if self._client is None:
            self._client = chromadb.PersistentClient(path=str(self._chroma_path))
        return self._client

    def run(self, course_id: str | None = None) -> dict[str, dict[str, int]]:
        """
        执行回填。

        Args:
            course_id: 指定课程（ChromaDB collection name），None 则全部课程。

        Returns:
            {collection_name: {"chunks": N, "docs": M}}
        """
        collections = self.client.list_collections()
        stats: dict[str, dict[str, int]] = {}

        for col in collections:
            name = col.name
            if name == "nexus_chunks":  # skip default/system collection
                continue
            if course_id and name != course_id:
                continue

            # 先确保 documents 行存在（FK 约束要求 chunks.document_id 有对应行）
            doc_count = self._ensure_documents(name)
            chunk_count = self._backfill_collection(name)
            # 最后用实际 chunk 数更新 document chunk_count
            self._update_document_counts(name)
            stats[name] = {"chunks": chunk_count, "docs": doc_count}

        return stats

    def _backfill_collection(self, collection_name: str) -> int:
        """回填单个 collection 的全部 chunks，返回写入数。"""
        col = self.client.get_collection(collection_name)
        # 分批拉取（ChromaDB get() 默认最多返回 N 条，需显式指定较大 limit）
        result = col.get(limit=100_000, include=["metadatas"])

        chunk_ids = result.get("ids", [])
        metadatas = result.get("metadatas", [])

        if not chunk_ids:
            logger.info("Collection %s has 0 chunks — skipping", collection_name)
            return 0

        count = 0
        for chunk_id, meta in zip(chunk_ids, metadatas):
            if meta is None:
                logger.warning("Chunk %s has no metadata — skipping", chunk_id)
                continue

            document_id = self._derive_document_id(meta, collection_name)
            self._store.upsert_chunk(
                chunk_id=chunk_id,
                document_id=document_id,
                course_id=meta.get("course_id", collection_name),
                topic=meta.get("topic", "Unknown"),
                chunk_type=meta.get("type"),
                keywords=_split_csv(meta.get("keywords")),
                prerequisites=_split_csv(meta.get("prerequisites")),
                page=self._safe_int(meta.get("page")),
                lecture_number=self._safe_int(meta.get("lecture_number")),
            )
            count += 1

        logger.info("Backfilled %d chunks from collection %s", count, collection_name)
        return count

    def _ensure_documents(self, collection_name: str) -> int:
        """
        确保 documents 表中存在 ChromaDB 集合里出现的每个唯一文档行。
        返回文档数。必须在 _backfill_collection 之前调用（FK 约束）。
        """
        col = self.client.get_collection(collection_name)
        result = col.get(limit=100_000, include=["metadatas"])

        seen: set[str] = set()
        for meta in result.get("metadatas", []):
            if meta is None:
                continue
            doc_id = self._derive_document_id(meta, collection_name)
            if doc_id in seen:
                continue
            seen.add(doc_id)

            existing = self._store.get_document(doc_id)
            if existing is None:
                self._store.upsert_document(
                    doc_id=doc_id,
                    course_id=meta.get("course_id", collection_name),
                    filename=meta.get("source_file", "unknown"),
                    file_hash="backfill",
                    parse_status="done",
                    chunk_count=0,
                )

        return len(seen)

    def _update_document_counts(self, collection_name: str) -> int:
        """
        用实际 chunk 数更新 documents 表的 chunk_count。
        假设 _ensure_documents 已先调用。
        """
        counts: Counter[str] = Counter()

        col = self.client.get_collection(collection_name)
        result = col.get(limit=100_000, include=["metadatas"])

        for meta in result.get("metadatas", []):
            if meta is None:
                continue
            doc_id = self._derive_document_id(meta, collection_name)
            counts[doc_id] += 1

        for doc_id, count in counts.items():
            existing = self._store.get_document(doc_id)
            self._store.set_document_status(
                doc_id=doc_id,
                status=existing["parse_status"] if existing else "done",
                chunk_count=count,
            )

        return len(counts)

    def _derive_document_id(self, meta: dict, collection_name: str) -> str:
        """从 chunk metadata 推导 document_id: {course_id}/{source_file}"""
        course_id = meta.get("course_id", collection_name)
        source_file = meta.get("source_file", "unknown")
        return f"{course_id}/{source_file}"

    # ────────────────────────────────────────────────
    # Phase M1-4: 概念提取 pipeline
    # ────────────────────────────────────────────────

    def run_extraction(
        self,
        course_id: str,
        llm_api_key: str | None = None,
    ) -> dict[str, int] | None:
        """
        对指定课程运行概念提取管线。

        流程:
        1. 从 SQLite chunks 表读取元数据 → 重建 ParsedChunk
        2. Pass 1: 规则提取（零 LLM 成本）
        3. Pass 2: LLM 精炼（有 key 时）
        4. 持久化到 concepts / chunk_concepts / concept_relations 表

        Returns:
            {"concepts": N, "relations": M} 或 None（无 chunks 时）
        """
        import json

        from .concept_extractor import ConceptExtractor
        from .parser import ParsedChunk

        # Step 1: 从 SQLite 读取 chunks
        rows = self._store.get_chunks_by_course(course_id)
        if not rows:
            logger.info("Course %s has 0 SQLite chunks — skipping extraction", course_id)
            return None

        chunks: list[ParsedChunk] = []
        chunk_ids: list[str] = []

        for row in rows:
            # 解析 JSON 存储的 keywords / prerequisites
            try:
                keywords = json.loads(row["keywords"]) if isinstance(row["keywords"], str) else (row["keywords"] or [])
            except (json.JSONDecodeError, TypeError):
                keywords = []

            try:
                prerequisites = json.loads(row["prerequisites"]) if isinstance(row["prerequisites"], str) else (row["prerequisites"] or [])
            except (json.JSONDecodeError, TypeError):
                prerequisites = []

            pc = ParsedChunk(
                topic=row["topic"],
                content="",  # 概念提取不需要全文
                type=row["type"] or "overview",
                keywords=keywords,
                prerequisites=prerequisites,
                page=row.get("page"),
            )
            chunks.append(pc)
            chunk_ids.append(row["chunk_id"])

        logger.info(
            "Reconstructed %d ParsedChunks from SQLite for course %s",
            len(chunks), course_id,
        )

        # Step 2: 实例化 ConceptExtractor
        extractor = ConceptExtractor(self._store, llm_api_key=llm_api_key)

        # Step 3: Pass 1 — 规则提取（零 LLM 成本）
        candidates = extractor.extract_from_chunks(chunks, chunk_ids, course_id)
        logger.info("Pass 1: %d candidate concepts from %d chunks", len(candidates), len(chunks))

        # Step 4: Pass 2 — LLM 精炼（有 key 时）
        if llm_api_key:
            refined, relations = extractor.refine_with_llm(
                candidates, chunks, chunk_ids, course_id
            )
            logger.info(
                "Pass 2: %d refined concepts, %d relations", len(refined), len(relations)
            )
        else:
            refined = candidates
            relations = []
            logger.info("No LLM key — skipping Pass 2/3, using Pass 1 candidates only")

        # Step 5: 持久化
        stats = extractor.persist(refined, relations)
        logger.info(
            "Persisted: %d concepts, %d relations for %s",
            stats["concepts"], stats["relations"], course_id,
        )
        return stats

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
