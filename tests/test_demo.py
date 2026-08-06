from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from nexus.context_api import build_context
from nexus.demo import DEMO_MARKER, create_demo_runtime


def test_demo_runtime_builds_complete_synthetic_context(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    now = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)

    result = create_demo_runtime(data_dir, now=now)
    context = build_context(data_dir=data_dir, course_id="CS202_OS", now=now)

    assert result["ok"] is True
    assert result["task_count"] == 2
    assert result["learning_available"] is True
    assert result["evidence_count"] == 1
    assert (data_dir / DEMO_MARKER).is_file()
    assert context["sync"]["sources"]["brightspace"]["configured"] is True
    assert context["sync"]["sources"]["brightspace"]["status"] == "succeeded"
    assert context["briefing"]["available"] is True
    assert context["learning"]["evidence"][0]["source"] == "demo_assessment"


def test_demo_runtime_refuses_nonempty_destination(tmp_path: Path) -> None:
    data_dir = tmp_path / "personal-data"
    data_dir.mkdir()
    (data_dir / "tasks.json").write_text(json.dumps([{"id": "personal"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        create_demo_runtime(data_dir)

    assert json.loads((data_dir / "tasks.json").read_text(encoding="utf-8"))[0]["id"] == "personal"
