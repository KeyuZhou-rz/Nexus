from __future__ import annotations

from pathlib import Path

from nexus.knowledge.ingest import ingest_markdown_files


class _StoreStub:
    def __init__(self):
        self.calls = []

    def upsert_chunks(self, ids, texts, metadatas):
        self.calls.append((ids, texts, metadatas))


def test_ingest_markdown_files(tmp_path):
    file_path = tmp_path / "lecture.md"
    file_path.write_text("# Title\n\nThis is a chunk test.\n\nAnother paragraph.", encoding="utf-8")

    store = _StoreStub()
    summary = ingest_markdown_files(
        [Path(file_path)],
        store,
        course_id="EE201",
        doc_type="lecture_slide",
        max_chars=50,
        overlap=10,
    )

    assert summary.files == 1
    assert summary.chunks >= 1
    assert len(store.calls) == 1
    _, _, metas = store.calls[0]
    assert metas[0]["course_id"] == "EE201"
    assert "source_path" in metas[0]
    assert "session_id" in metas[0]
