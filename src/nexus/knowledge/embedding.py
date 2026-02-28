from __future__ import annotations

import hashlib
from typing import Iterable


class HashEmbeddingFunction:
    """Deterministic local embedding function without external model downloads."""

    def __init__(self, dimension: int = 64) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be > 0")
        self.dimension = dimension

    def _embed_one(self, text: str) -> list[float]:
        base = (text or "").encode("utf-8")
        vector: list[float] = []
        for idx in range(self.dimension):
            digest = hashlib.sha256(base + f"::{idx}".encode("utf-8")).digest()
            # Map first 4 bytes to [-1, 1]
            value = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
            vector.append((value * 2.0) - 1.0)
        return vector

    def __call__(self, input: Iterable[str]) -> list[list[float]]:  # chroma embedding fn interface
        return [self._embed_one(item) for item in input]
