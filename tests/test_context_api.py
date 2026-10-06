from __future__ import annotations

import json
from pathlib import Path

from nexus.core.context_api import build_context
from nexus.learning_focus import knowledge_learning
from nexus.core.io_utils import atomic_write_json
from nexus.knowledge.memory_api import MemoryAPI
from nexus.knowledge.sqlite_store import SQLiteStore


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
    assert payload["next_action"]["available"] is True
    assert payload["next_action"]["task_id"] == "gcal:1"
    assert payload["next_action"]["suggestion"]
    assert payload["sync"]["sources"]["google"]["status"] == "succeeded"
    assert payload["sync"]["sources"]["google"]["configured"] is False
    assert payload["learning"]["available"] is False
    assert (tmp_path / "tasks.json").read_text(encoding="utf-8") == before


def test_context_can_be_serialized_for_an_assistant(tmp_path: Path) -> None:
    payload = build_context(data_dir=tmp_path, learning=knowledge_learning)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "knowledge_store_not_initialized" in encoded
    assert payload["next_action"]["available"] is False


def test_context_exposes_bounded_learning_evidence_without_question_text(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_concept("os__deadlock", "Deadlock", "OS")
    MemoryAPI(store).record_event(
        "os__deadlock",
        "struggled",
        context={
            "evidence_type": "assessment",
            "source": "offline_quiz",
            "confidence": 0.91,
            "question": "Sensitive question text",
        },
    )

    payload = build_context(data_dir=tmp_path, course_id="OS", learning=knowledge_learning)
    evidence = payload["learning"]["evidence"]

    assert len(evidence) == 1
    assert evidence[0]["source"] == "offline_quiz"
    assert evidence[0]["trust_status"] == "trusted"
    assert evidence[0]["timestamp"]
    assert "question" not in json.dumps(evidence)


def test_context_combines_course_task_with_learning_focus(tmp_path: Path) -> None:
    atomic_write_json(
        tmp_path / "tasks.json",
        [{
            "schema_version": "1.0",
            "id": "lms:lab",
            "title": "Deadlock lab",
            "due_at": "2026-08-09T09:00:00+00:00",
            "source": "brightspace",
            "course": "OS",
            "status": "open",
            "priority": 2,
            "tags": ["assignment"],
        }],
    )
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_concept("os__deadlock", "Deadlock", "OS")
    MemoryAPI(store).record_event(
        "os__deadlock",
        "struggled",
        context={"evidence_type": "assessment", "source": "quiz"},
    )

    payload = build_context(data_dir=tmp_path, course_id="OS", learning=knowledge_learning)
    focus = payload["learning"]["focus"]

    assert focus[0]["name"] == "Deadlock"
    assert focus[0]["related_task"]["id"] == "lms:lab"
    assert focus[0]["evidence_source"] == "quiz"


def test_context_exposes_bounded_last_sync_receipt(tmp_path: Path) -> None:
    atomic_write_json(tmp_path / "sync_receipt.json", {
        "status": "succeeded",
        "trigger": "scheduler",
        "finished_at": "2026-08-07T01:00:00+00:00",
        "duration_seconds": 1.2,
        "summary": {"task_count": 3, "linked_focus_count": 1},
        "sources": {"brightspace": {"error": "secret"}},
    })

    payload = build_context(data_dir=tmp_path)

    assert payload["sync"]["last_sync"]["status"] == "succeeded"
    assert payload["sync"]["last_sync"]["summary"]["linked_focus_count"] == 1
    assert "secret" not in json.dumps(payload)


def test_context_treats_non_utf8_sync_receipt_as_unavailable(tmp_path: Path) -> None:
    (tmp_path / "sync_receipt.json").write_bytes(b"\xffnot-json")

    payload = build_context(data_dir=tmp_path)

    assert payload["sync"]["last_sync"] is None
