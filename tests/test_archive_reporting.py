from __future__ import annotations

import json

from nexus.archive_sync.reporting import build_failure_report, save_failure_report


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
