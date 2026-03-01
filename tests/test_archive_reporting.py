from __future__ import annotations

import json

from nexus.archive_sync.reporting import (
    build_failure_report,
    load_failure_queue,
    save_failure_report,
)


def test_build_failure_report_contains_count():
    failures = [{"course": "OS", "error": "timeout"}]
    payload = build_failure_report(failures)
    assert payload["failure_count"] == 1
    assert payload["failures"] == failures
    assert "generated_at" in payload


def test_save_failure_report_writes_json(tmp_path):
    path = tmp_path / "archive_failures.json"
    failures = [{"course": "OS", "error": "timeout"}]
    save_failure_report(path, failures)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["failure_count"] == 1
    assert payload["failures"][0]["course"] == "OS"


def test_load_failure_queue_reads_failures(tmp_path):
    path = tmp_path / "archive_failures.json"
    save_failure_report(path, [{"course": "OS", "error": "timeout"}])
    failures = load_failure_queue(path)
    assert len(failures) == 1
    assert failures[0]["course"] == "OS"
