from __future__ import annotations

import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from nexus.knowledge import ingestor as ingestor_module
from nexus.knowledge.ingestor import CourseIngestor, IngestResult
from nexus.knowledge.maintenance_lock import document_ingest_lock


def test_course_ingestor_uses_canonical_per_document_lease(
    tmp_path: Path, monkeypatch
) -> None:
    ingestor = CourseIngestor.__new__(CourseIngestor)
    ingestor._sqlite = SimpleNamespace(db_path=tmp_path / "nexus.db")
    ingestor._chroma = SimpleNamespace(persist_dir=tmp_path / "chroma")
    calls: list[tuple[str, dict]] = []

    @contextmanager
    def fake_maintenance_lock(**kwargs):
        calls.append(("maintenance", kwargs))
        yield tmp_path / "maintenance.lock"

    @contextmanager
    def fake_document_lock(**kwargs):
        calls.append(("document", kwargs))
        yield tmp_path / "document.lock"

    expected = IngestResult(tmp_path / "lecture.pdf", "CS101", "skipped")
    monkeypatch.setattr(ingestor_module, "maintenance_lock", fake_maintenance_lock)
    monkeypatch.setattr(ingestor_module, "document_ingest_lock", fake_document_lock)
    monkeypatch.setattr(
        ingestor,
        "_ingest_file_unlocked",
        lambda filepath, course_id, *, force: expected,
    )

    assert ingestor.ingest_file(tmp_path / "lecture.pdf", "CS101") is expected
    assert calls[0][0] == "maintenance"
    assert calls[1] == (
        "document",
        {
            "sqlite_path": tmp_path / "nexus.db",
            "chroma_path": tmp_path / "chroma",
            "course_id": "CS101",
            "document_id": "CS101/lecture.pdf",
        },
    )


def test_document_lock_blocks_the_same_document_but_not_another(
    tmp_path: Path,
) -> None:
    sqlite_path = tmp_path / "nexus.db"
    chroma_path = tmp_path / "chroma"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = """
import sys
import time
from pathlib import Path
from nexus.knowledge.maintenance_lock import document_ingest_lock

sqlite_path, chroma_path, ready, release = map(Path, sys.argv[1:])
with document_ingest_lock(
    sqlite_path=sqlite_path,
    chroma_path=chroma_path,
    course_id='CS101',
    document_id='CS101/lecture.pdf',
):
    ready.touch()
    deadline = time.time() + 15
    while not release.exists():
        if time.time() > deadline:
            raise TimeoutError('release gate not opened')
        time.sleep(0.005)
"""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(sqlite_path),
            str(chroma_path),
            str(ready),
            str(release),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 15
        while not ready.exists():
            if time.time() > deadline:
                raise TimeoutError("holder did not acquire the document lease")
            time.sleep(0.005)

        entered = threading.Event()

        def acquire_same_document() -> None:
            with document_ingest_lock(
                sqlite_path=sqlite_path,
                chroma_path=chroma_path,
                course_id="CS101",
                document_id="CS101/lecture.pdf",
            ):
                entered.set()

        waiter = threading.Thread(target=acquire_same_document)
        waiter.start()
        time.sleep(0.05)
        assert not entered.is_set()

        with document_ingest_lock(
            sqlite_path=sqlite_path,
            chroma_path=chroma_path,
            course_id="CS101",
            document_id="CS101/other.pdf",
            timeout=0.2,
        ):
            pass

        release.touch()
        waiter.join(timeout=15)
        assert entered.is_set()
    finally:
        release.touch()
        stdout, stderr = holder.communicate(timeout=30)
        assert holder.returncode == 0, (stdout, stderr)
