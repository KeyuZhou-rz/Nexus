from __future__ import annotations


def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    """Split text into overlapping chunks for retrieval."""
    content = (text or "").strip()
    if not content:
        return []

    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= max_chars:
        raise ValueError("overlap must be < max_chars")

    parts = [part.strip() for part in content.split("\n\n") if part.strip()]
    chunks: list[str] = []
    cursor = ""

    for part in parts:
        if len(part) > max_chars:
            start = 0
            step = max_chars - overlap
            while start < len(part):
                window = part[start : start + max_chars].strip()
                if window:
                    if cursor:
                        chunks.append(cursor)
                        cursor = ""
                    chunks.append(window)
                start += step
            continue

        candidate = f"{cursor}\n\n{part}".strip() if cursor else part
        if len(candidate) <= max_chars:
            cursor = candidate
            continue

        if cursor:
            chunks.append(cursor)

        if overlap > 0 and chunks:
            prev_tail = chunks[-1][-overlap:]
            cursor = f"{prev_tail}\n\n{part}".strip()
            if len(cursor) > max_chars:
                chunks.append(part)
                cursor = ""
        else:
            cursor = part

    if cursor:
        chunks.append(cursor)

    return chunks
