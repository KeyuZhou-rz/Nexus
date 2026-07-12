from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from nexus.cli.ingest import _run_phase3
from nexus.knowledge.ingestor import IngestResult


def _args(input_path: Path, tmp_path: Path) -> Namespace:
    return Namespace(
        input=str(input_path),
        course_id="CS_TEST",
        db_dir=str(tmp_path / "chroma"),
        sqlite_path=str(tmp_path / "nexus.db"),
        force=False,
    )


def test_phase3_ingest_returns_failure_for_any_failed_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    class FakeIngestor:
        def __init__(self, **kwargs) -> None:
            pass

        def ingest_file(self, filepath, course_id, *, force=False):
            if filepath.name == "first.pdf":
                return IngestResult(filepath, course_id, "ingested", chunks=3)
            return IngestResult(filepath, course_id, "failed", error="parse failed")

    monkeypatch.setattr("nexus.knowledge.ingestor.CourseIngestor", FakeIngestor)

    assert _run_phase3(_args(tmp_path, tmp_path)) == 1
    output = capsys.readouterr().out
    assert "[FAIL] second.pdf" in output
    assert "成功 1，跳过 0，失败 1" in output


def test_phase3_ingest_succeeds_when_files_are_ingested_or_skipped(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    class FakeIngestor:
        def __init__(self, **kwargs) -> None:
            pass

        def ingest_file(self, filepath, course_id, *, force=False):
            status = "ingested" if filepath.name == "first.pdf" else "skipped"
            return IngestResult(filepath, course_id, status, chunks=2)

    monkeypatch.setattr("nexus.knowledge.ingestor.CourseIngestor", FakeIngestor)

    assert _run_phase3(_args(tmp_path, tmp_path)) == 0
    assert "成功 1，跳过 1，失败 0" in capsys.readouterr().out
