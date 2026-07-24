from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

from nexus.cli import db_backup
from nexus.cli.sessions import main as sessions_main
from nexus.cli.status import main as status_main
from nexus.failure_queue import update_failure_queue
from nexus.io_utils import atomic_write_json
from nexus.knowledge.sqlite_store import SQLiteStore
from nexus.runtime_status import build_runtime_status


def test_runtime_status_reads_reports_queues_and_session_backlog(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_session(
        session_id="retry-1",
        started_at="2026-07-24T08:00:00",
        last_active="2026-07-24T08:00:00",
        course_id="CS101",
        interaction_count=2,
        ended=False,
    )
    store.claim_session_end("retry-1")
    store.release_session_retry("retry-1", "temporary outage", "2026-07-24T09:00:00")
    atomic_write_json(tmp_path / "pipeline_report.json", {"ok": True, "generated_at": "2026-07-24T08:30:00"})
    atomic_write_json(tmp_path / "session_finalization_report.json", {"ok": True, "count": 3})
    atomic_write_json(tmp_path / "backup_report.json", {"ok": True, "archive": "backup.zip"})
    update_failure_queue(
        tmp_path / "ingest_failures.json",
        [{"operation": "ingest", "course_id": "CS101", "input_path": "bad.pdf", "error": "parse failed"}],
    )

    status = build_runtime_status(tmp_path)

    assert status["pipeline"]["ok"] is True
    assert status["session_finalization"]["count"] == 3
    assert status["backup"]["archive"] == "backup.zip"
    assert status["session_backlog"]["by_status"] == {"retry": 1}
    assert status["failure_queues"]["ingest"]["pending_count"] == 1
    assert status["failure_queues"]["archive"]["pending_count"] == 0


def test_status_cli_is_json_and_does_not_require_a_database(tmp_path, capsys) -> None:
    assert status_main(["--data-dir", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pipeline"]["available"] is False
    assert payload["session_backlog"]["available"] is False


def test_scheduler_commands_persist_session_and_backup_outcomes(tmp_path, monkeypatch) -> None:
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_session(
        session_id="idle-1",
        started_at="2026-07-24T08:00:00",
        last_active=(datetime.now() - timedelta(minutes=10)).isoformat(),
        course_id=None,
        interaction_count=0,
        ended=False,
    )
    assert sessions_main(["--db-path", str(store.db_path), "finalize-idle", "--json"]) == 0
    session_report = json.loads((tmp_path / "session_finalization_report.json").read_text())
    assert session_report["ok"] is True
    assert session_report["count"] == 1

    monkeypatch.setattr(
        "nexus.knowledge.maintenance.create_backup",
        lambda *_args, **_kwargs: {"ok": True, "archive": "backup.zip"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["db-backup", "--sqlite-path", str(tmp_path / "nexus.db"), "--db-dir", str(tmp_path / "chroma")],
    )
    assert db_backup.main() == 0
    backup_report = json.loads((tmp_path / "backup_report.json").read_text())
    assert backup_report["ok"] is True
    assert backup_report["command"] == "create"
