from __future__ import annotations


def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")

    content = text.strip()
    if not content:
        return []

    if len(content) <= max_chars:
        return [content]

    chunks: list[str] = []
    start = 0
    n = len(content)
    while start < n:
        end = min(start + max_chars, n)
        chunks.append(content[start:end])
        if end == n:
            break
        start = end - overlap
    return chunks
