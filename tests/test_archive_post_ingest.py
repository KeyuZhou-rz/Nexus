from __future__ import annotations

from pathlib import Path

from nexus.archive_sync.post_ingest import run_archive_post_ingest
from nexus.state_store import load_state


class _StoreStub:
    def __init__(self) -> None:
        self.calls = []

    def upsert_chunks(self, ids, texts, metadatas):
        self.calls.append((ids, texts, metadatas))


def test_run_archive_post_ingest_ingests_text_and_updates_state(tmp_path: Path):
    archive_file = tmp_path / "calc_notes.txt"
    archive_file.write_text(
        """
Common mistakes: chain rule sign in implicit differentiation.
A review line.
""".strip(),
        encoding="utf-8",
    )

    archives = [
        {
            "course": "MATH-UA 123",
            "archived_path": str(archive_file),
        }
    ]

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    stub = _StoreStub()
    summary = run_archive_post_ingest(
        archives=archives,
        data_dir=data_dir,
        db_dir=tmp_path / "chroma",
        store=stub,
        max_chars=80,
        overlap=10,
    )

    assert summary["status"] == "success"
    assert summary["files_scanned"] == 1
    assert summary["files_ingested"] == 1
    assert summary["chunks"] >= 1
    assert summary["weak_candidates"] >= 1
    assert len(stub.calls) == 1

    state = load_state(data_dir / "state.json")
    assert any("chain rule" in topic for topic in state.weak_points)
