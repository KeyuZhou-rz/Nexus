from __future__ import annotations

import json
from pathlib import Path

from nexus.context_api import build_context
from nexus.io_utils import atomic_write_json


def test_build_context_is_stable_and_read_only_for_empty_runtime(tmp_path: Path) -> None:
    atomic_write_json(
        tmp_path / "tasks.json",
        [{
            "schema_version": "1.0",
            "id": "gcal:1",
            "title": "Operating systems exam",
            "due_at": "2026-08-08T09:00:00+00:00",
            "source": "gcal",
            "course": "CS202_OS",
            "status": "open",
            "priority": 3,
            "tags": ["exam"],
        }],
    )
    atomic_write_json(
        tmp_path / "pipeline_report.json",
        {
            "ok": True,
            "generated_at": "2026-08-05T00:00:00+00:00",
            "sources": {"google": "succeeded"},
        },
    )
    before = (tmp_path / "tasks.json").read_text(encoding="utf-8")

    payload = build_context(data_dir=tmp_path, course_id="CS202_OS")

    assert payload["schema_version"] == "1.0"
    assert payload["scope"]["course_id"] == "CS202_OS"
    assert payload["tasks"]["count"] == 1
    assert payload["tasks"]["items"][0]["id"] == "gcal:1"
    assert payload["sync"]["sources"]["google"]["status"] == "succeeded"
    assert payload["sync"]["sources"]["google"]["configured"] is False
    assert payload["learning"]["available"] is False
    assert (tmp_path / "tasks.json").read_text(encoding="utf-8") == before


def test_context_can_be_serialized_for_an_assistant(tmp_path: Path) -> None:
    payload = build_context(data_dir=tmp_path)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "knowledge_store_not_initialized" in encoded
