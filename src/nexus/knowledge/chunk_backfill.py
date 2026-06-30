"""
ChromaDB → SQLite Chunks 回填引擎 — Nexus Phase M1-2

从 ChromaDB 集合读取已有 vector chunks，将其结构化元数据写入
SQLite chunks 表，同时更新 documents 表的 chunk_count。

幂等：INSERT OR REPLACE 保证重复执行安全。
"""
from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import chromadb

from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
