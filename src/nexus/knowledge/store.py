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
        name = name.ljust(3, "_")
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

        coll = (
            self.get_or_create_course_collection(course_id)
            if self._per_course and course_id
            else self.collection
        )
        coll.upsert(ids=ids, documents=texts, metadatas=metadatas)

    def delete_chunks_by_ids(self, ids: list[str], course_id: str | None = None) -> None:
        """
        按 chunk_id 列表删除 ChromaDB 中的向量记录。
        文件更新后重新 ingest 时先调用此方法清理旧数据。
        """
        if not ids:
            return
        coll = (
            self.get_or_create_course_collection(course_id)
            if self._per_course and course_id
            else self.collection
        )
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
        coll = (
            self.get_or_create_course_collection(course_id)
            if self._per_course and course_id
            else self.collection
        )
        return coll.query(query_texts=[text], n_results=n_results, where=where)

    def fetch_chunks(
        self,
        *,
        where: dict[str, Any] | None = None,
        limit: int = 20,
        course_id: str | None = None,
    ) -> dict[str, Any]:
        """按 metadata 条件精确获取 chunks（不走向量搜索）。"""
        coll = (
            self.get_or_create_course_collection(course_id)
            if self._per_course and course_id
            else self.collection
        )
        return coll.get(where=where, include=["documents", "metadatas"], limit=limit)
