from __future__ import annotations

import pytest

from nexus.knowledge.chunking import chunk_text


def test_chunk_text_basic_split():
    text = "A" * 700 + "\n\n" + "B" * 700
    chunks = chunk_text(text, max_chars=900, overlap=100)
    assert len(chunks) >= 2


def test_chunk_text_invalid_overlap():
    with pytest.raises(ValueError):
        chunk_text("hello", max_chars=100, overlap=100)
