from __future__ import annotations

from nexus.knowledge.embedding import HashEmbeddingFunction


def test_hash_embedding_deterministic():
    emb = HashEmbeddingFunction(dimension=8)
    first = emb(["hello"])[0]
    second = emb(["hello"])[0]
    assert first == second
    assert len(first) == 8


def test_hash_embedding_changes_with_text():
    emb = HashEmbeddingFunction(dimension=8)
    a = emb(["a"])[0]
    b = emb(["b"])[0]
    assert a != b
