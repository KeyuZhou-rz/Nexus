from __future__ import annotations

def chunk_text(text: str) -> list[str]:
    content = text.strip()
    if not content:
        return []
    

    parts = [part.strip() for part in content.split("\n\n") if part.strip()]

    return parts
