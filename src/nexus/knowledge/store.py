"""
ChromaDB 存储层 — Nexus Phase 3

设计原则：
- Per-Course Collection：每门课一个独立 collection，物理隔离避免跨课程混淆。
  命名规范: "{course_id}"，例如 "CS202_OS"、"CS305_Database"
- 使用 ChromaDB 默认 Embedding（all-MiniLM-L6-v2）进行语义向量化。
  取代原 HashEmbeddingFunction（哈希伪向量不具备语义相似度）。
- ChromaDB metadata 不支持 list 类型，keywords/prerequisites 须存为逗号分隔字符串。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class CourseCollectionNotFoundError(LookupError):
    """Raised when a requested course has no indexed Chroma collection."""


def _make_collection_name(course_id: str) -> str:
    """
    将 course_id 转换为合法的 ChromaDB collection 名称。
    ChromaDB 要求：3-63 字符，只含字母数字和下划线/连字符，不以连字符开头/结尾。
    """
    import re
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", course_id)
    # 确保长度合法
    name = name[:63]
    if len(name) < 3:
        name = name.ljust(3, "x")
    return name


class ChromaKnowledgeStore:
    """
    ChromaDB 封装，支持 per-course collection。

    per_course=True（默认）：
        collection 名称 = course_id，每门课物理隔离，检索精度更高。
    per_course=False：
        使用固定 collection_name（向后兼容旧代码路径）。
    """

    def __init__(
        self,
        persist_dir: Path,
        collection_name: str = "nexus_chunks",
        per_course: bool = False,
    ) -> None:
        self.persist_dir = persist_dir
        self._base_collection_name = collection_name
        self._per_course = per_course

        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "chromadb is required. Install with: pip install chromadb"
            ) from exc

        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # PersistentClient: 数据落盘到 persist_dir，重启后不丢失
        self._client = chromadb.PersistentClient(path=str(self.persist_dir))

        # 使用默认 embedding function（all-MiniLM-L6-v2）
        # 首次使用时 ChromaDB 会自动下载模型（~80MB），后续从本地缓存加载
        # 提供真正的语义相似度，取代原来的 HashEmbeddingFunction
        self._default_ef = chromadb.utils.embedding_functions.DefaultEmbeddingFunction()

        # 默认 collection（per_course=False 时使用）
        if not per_course:
            self.collection = self._client.get_or_create_collection(
                name=self._base_collection_name,
                embedding_function=self._default_ef,
                metadata={"hnsw:space": "cosine"},  # 使用余弦距离，语义检索更准确
            )
        else:
            self.collection = None  # per_course 模式下按需创建

    # ────────────────────────────────────────────────
    # Per-course collection 管理
    # ────────────────────────────────────────────────

    def get_or_create_course_collection(self, course_id: str):
        """
        获取或创建指定课程的 ChromaDB collection。
        collection 名称由 course_id 生成，确保合法（见 _make_collection_name）。
        metadata 中存储课程信息，方便后续管理和清理。
        """
        name = _make_collection_name(course_id)
        return self._client.get_or_create_collection(
            name=name,
            embedding_function=self._default_ef,
            metadata={
                "hnsw:space": "cosine",
                "course_id": course_id,
            },
        )

    def get_course_collection(self, course_id: str):
        """Return an existing course collection without creating an empty one."""
        name = _make_collection_name(course_id)
        try:
            collection = self._client.get_collection(
                name=name,
                embedding_function=self._default_ef,
            )
        except Exception as exc:
            raise CourseCollectionNotFoundError(
                f"No indexed collection found for course {course_id!r}"
            ) from exc

        indexed_course = (collection.metadata or {}).get("course_id")
        if indexed_course and indexed_course != course_id:
            raise CourseCollectionNotFoundError(
                f"Collection name collision for course {course_id!r}"
            )
        return collection

    def _course_collections(self) -> list[Any]:
        """List indexed per-course collections, excluding legacy/default collections."""
        collections: list[Any] = []
        for listed in self._client.list_collections():
            collection = listed
            if not hasattr(collection, "query"):
                name = listed if isinstance(listed, str) else listed.name
                collection = self._client.get_collection(
                    name=name,
                    embedding_function=self._default_ef,
                )
            if (getattr(collection, "metadata", None) or {}).get("course_id"):
                collections.append(collection)
        return collections

    def _collection_for_write(self, course_id: str | None):
        if self._per_course:
            if not course_id:
                raise ValueError("course_id is required when writing in per-course mode")
            return self.get_or_create_course_collection(course_id)
        return self.collection

    def _collections_for_read(self, course_id: str | None) -> list[Any]:
        if not self._per_course:
            return [self.collection]
        if course_id:
            return [self.get_course_collection(course_id)]
        return self._course_collections()

    def delete_course_collection(self, course_id: str) -> None:
        """
        删除课程 collection（学期结束清理，或文件重新 ingest 前的强制重置）。
        ChromaDB 删除 collection 会同时清除所有向量数据。
        """
        name = _make_collection_name(course_id)
        try:
            self._client.delete_collection(name)
        except Exception:
            pass  # collection 不存在时静默忽略

    # ────────────────────────────────────────────────
    # 数据写入
    # ────────────────────────────────────────────────

    def upsert_chunks(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
        course_id: str | None = None,
    ) -> None:
        """
        批量 upsert chunks 到 ChromaDB。
        - course_id 非空且 per_course=True：写入对应课程 collection
        - 否则写入默认 collection（向后兼容）
        注意：metadata 中 keywords/prerequisites 须为字符串，不能是 list。
        """
        if not (len(ids) == len(texts) == len(metadatas)):
            raise ValueError("ids/texts/metadatas lengths must match")
        if not ids:
            return

        coll = self._collection_for_write(course_id)
        coll.upsert(ids=ids, documents=texts, metadatas=metadatas)

    def delete_chunks_by_ids(self, ids: list[str], course_id: str | None = None) -> None:
        """
        按 chunk_id 列表删除 ChromaDB 中的向量记录。
        文件更新后重新 ingest 时先调用此方法清理旧数据。
        """
        if not ids:
            return
        if self._per_course and course_id:
            coll = self.get_course_collection(course_id)
        elif self._per_course:
            raise ValueError("course_id is required when deleting in per-course mode")
        else:
            coll = self.collection
        coll.delete(ids=ids)

    # ────────────────────────────────────────────────
    # 查询
    # ────────────────────────────────────────────────

    def query(
        self,
        text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
        course_id: str | None = None,
    ) -> dict[str, Any]:
        """
        语义相似度查询。
        - course_id 非空且 per_course=True：限定在该课程 collection 内检索
        - n_results: 返回最相似的 top-N chunks
        """
        combined: list[tuple[str, str, dict[str, Any], float]] = []
        for coll in self._collections_for_read(course_id):
            result = coll.query(query_texts=[text], n_results=n_results, where=where)
            ids = (result.get("ids") or [[]])[0]
            docs = (result.get("documents") or [[]])[0]
            metas = (result.get("metadatas") or [[]])[0]
            dists = (result.get("distances") or [[]])[0]
            combined.extend(zip(ids, docs, metas, dists))

        combined.sort(key=lambda item: item[3])
        selected = combined[:n_results]
        return {
            "ids": [[item[0] for item in selected]],
            "documents": [[item[1] for item in selected]],
            "metadatas": [[item[2] for item in selected]],
            "distances": [[item[3] for item in selected]],
        }

    def query_keywords(
        self,
        keywords: list[str],
        n_results: int = 5,
        course_id: str | None = None,
    ) -> dict[str, Any]:
        """Deterministic keyword lookup over normalized metadata tokens.

        Chroma metadata string filters do not provide portable substring
        semantics across supported versions. Read metadata once per collection
        and rank exact keyword/topic matches locally instead.
        """
        normalized = [keyword.strip().casefold() for keyword in keywords if keyword.strip()]
        if not normalized:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        ranked: list[tuple[str, str, dict[str, Any], float]] = []
        for coll in self._collections_for_read(course_id):
            count = coll.count()
            if count <= 0:
                continue
            result = coll.get(
                include=["documents", "metadatas"],
                limit=count,
            )
            for chunk_id, document, metadata in zip(
                result.get("ids") or [],
                result.get("documents") or [],
                result.get("metadatas") or [],
            ):
                meta = metadata or {}
                tokens = {
                    token.strip().casefold()
                    for token in str(meta.get("keywords", "")).split(",")
                    if token.strip()
                }
                topic = str(meta.get("topic", "")).casefold()
                exact_matches = sum(1 for keyword in normalized if keyword in tokens)
                topic_matches = sum(1 for keyword in normalized if keyword in topic)
                if exact_matches == 0 and topic_matches == 0:
                    continue
                relevance = exact_matches * 2 + topic_matches
                distance = max(0.20, 0.48 - relevance * 0.06)
                ranked.append((chunk_id, document, meta, distance))

        ranked.sort(key=lambda item: (item[3], item[0]))
        selected = ranked[:n_results]
        return {
            "ids": [[item[0] for item in selected]],
            "documents": [[item[1] for item in selected]],
            "metadatas": [[item[2] for item in selected]],
            "distances": [[item[3] for item in selected]],
        }

    def fetch_chunks(
        self,
        *,
        where: dict[str, Any] | None = None,
        limit: int = 20,
        course_id: str | None = None,
    ) -> dict[str, Any]:
        """按 metadata 条件精确获取 chunks（不走向量搜索）。"""
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        remaining = limit
        for coll in self._collections_for_read(course_id):
            if remaining <= 0:
                break
            result = coll.get(
                where=where,
                include=["documents", "metadatas"],
                limit=remaining,
            )
            current_ids = result.get("ids") or []
            current_docs = result.get("documents") or []
            current_metas = result.get("metadatas") or []
            ids.extend(current_ids)
            documents.extend(current_docs)
            metadatas.extend(current_metas)
            remaining -= len(current_ids)
        return {"ids": ids, "documents": documents, "metadatas": metadatas}
