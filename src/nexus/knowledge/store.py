from __future__ import annotations

from pathlib import Path
from typing import Any

from .embedding import HashEmbeddingFunction


class ChromaKnowledgeStore:
    """ChromaDB wrapper for Nexus knowledge chunks."""

    def __init__(
        self,
        persist_dir: Path,
        collection_name: str = "nexus_chunks",
        embedding_dimension: int = 64,
    ) -> None:
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.embedding_dimension = embedding_dimension

        try:
            import chromadb
        except ImportError as exc:  # pragma: no cover - runtime dependency guard
            raise RuntimeError(
                "chromadb is required for P2 ingestion. Install with: pip install chromadb"
            ) from exc

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.collection = client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=HashEmbeddingFunction(dimension=self.embedding_dimension),
        )

    def upsert_chunks(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not (len(ids) == len(texts) == len(metadatas)):
            raise ValueError("ids/texts/metadatas lengths must match")
        if not ids:
            return
        self.collection.upsert(ids=ids, documents=texts, metadatas=metadatas)

    def query(
        self,
        text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.collection.query(query_texts=[text], n_results=n_results, where=where)
