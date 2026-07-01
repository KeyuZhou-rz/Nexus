from __future__ import annotations

from pathlib import Path

from nexus.knowledge import ingest as ingest_module


class _StoreStub:
    def __init__(self):
        self.calls = []

    def upsert_chunks(self, ids, texts, metadatas):
        self.calls.append((ids, texts, metadatas))


def test_ingest_files_supports_multiple_suffixes(tmp_path: Path, monkeypatch):
    md_path = tmp_path / "a.md"
    pdf_path = tmp_path / "b.pdf"
    bin_path = tmp_path / "c.bin"

    md_path.write_text("# Title\n\nHello", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4")
    bin_path.write_bytes(b"binary")

    def _fake_extract(path: Path) -> str:
        if path.suffix.lower() == ".pdf":
            return "pdf text"
        if path.suffix.lower() == ".md":
            return path.read_text(encoding="utf-8")
        return ""

    monkeypatch.setattr(ingest_module, "extract_text", _fake_extract)

    store = _StoreStub()
    summary = ingest_module.ingest_files(
        [md_path, pdf_path, bin_path],
        store,
        course_id="EE201",
        doc_type="lecture_slide",
        max_chars=80,
        overlap=10,
    )

    assert summary.files == 2
    assert summary.chunks >= 2
    assert len(store.calls) == 2


def test_ingest_markdown_files_filters_non_markdown(tmp_path: Path, monkeypatch):
    md_path = tmp_path / "a.md"
    pdf_path = tmp_path / "b.pdf"
    md_path.write_text("hello", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF")

    seen: list[Path] = []

    def _fake_extract(path: Path) -> str:
        seen.append(path)
        return "text"

    monkeypatch.setattr(ingest_module, "extract_text", _fake_extract)

    store = _StoreStub()
    ingest_module.ingest_markdown_files(
        [md_path, pdf_path],
        store,
        course_id="EE201",
        doc_type="notes",
    )

    assert seen == [md_path]
