from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from nexus.context_api import build_context
from nexus.demo import DEMO_MARKER, create_demo_runtime
from nexus.storage import load_tasks


def test_demo_runtime_builds_complete_synthetic_context(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    now = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)

    result = create_demo_runtime(data_dir, now=now)
    context = build_context(data_dir=data_dir, course_id="CS202_OS", now=now)

    assert result["ok"] is True
    assert result["task_count"] == 4
    assert result["learning_available"] is True
    assert result["evidence_count"] == 1
    assert (data_dir / DEMO_MARKER).is_file()
    assert (data_dir / "judgment.json").is_file()
    assert context["sync"]["sources"]["brightspace"]["configured"] is True
    assert context["sync"]["sources"]["brightspace"]["status"] == "succeeded"
    assert context["briefing"]["available"] is True
    assert context["learning"]["evidence"][0]["source"] == "demo_assessment"
    ids = {item["id"] for item in context["tasks"]["items"]}
    all_ids = {task.id for task in load_tasks(data_dir / "tasks.json")}
    assert any(item_id.startswith("bspace_ical:") for item_id in ids)
    assert "demo:homework-1" in ids
    assert "demo:holiday" in all_ids
    assert "demo:holiday" not in ids
    assert context["learning"]["focus"][0]["related_task"]["id"] == "demo:homework-1"
    assert context["next_action"]["available"] is True
    assert context["next_action"]["task_id"] == "demo:homework-1"
    assert "截止" in (context["next_action"]["deadline"] or "")
    assert context["next_action"]["suggestion"]


def test_demo_runtime_records_assignment_deadline_change(tmp_path: Path) -> None:
    from nexus.academic_state import recent_changes

    data_dir = tmp_path / "week"
    create_demo_runtime(data_dir)
    kinds = [item["kind"] for item in recent_changes(data_dir, limit=20)]
    assert "due_changed" in kinds
    assert any("Assignment 4" in (item.get("text") or "") for item in recent_changes(data_dir, limit=20))


def test_demo_runtime_accepts_initialized_empty_directory(tmp_path: Path) -> None:
    from nexus.onboarding import initialize_runtime

    data_dir = tmp_path / "ready"
    initialize_runtime(data_dir)
    result = create_demo_runtime(data_dir)
    assert result["ok"] is True
    assert (data_dir / DEMO_MARKER).is_file()


def test_demo_runtime_refuses_nonempty_destination(tmp_path: Path) -> None:
    data_dir = tmp_path / "personal-data"
    data_dir.mkdir()
    (data_dir / "tasks.json").write_text(json.dumps([{"id": "personal"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        create_demo_runtime(data_dir)

    assert json.loads((data_dir / "tasks.json").read_text(encoding="utf-8"))[0]["id"] == "personal"
