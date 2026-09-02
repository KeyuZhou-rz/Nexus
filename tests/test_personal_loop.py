"""End-to-end personal-use loop: practice week → next action → start → done → next."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from nexus.action_service import ActionService
from nexus.cli import next_action as next_cli
from nexus.context_api import build_context, persist_next_action
from nexus.demo import create_demo_runtime, runtime_is_seedable
from nexus.intelligence.workload import build_judgment
from nexus.onboarding import initialize_runtime
from nexus.storage import load_tasks
from nexus.academic_state import tasks_from_obligations
from nexus.task_service import completed_task_ids, load_completions, mark_task_done, start_task


APP_PATH = Path(__file__).parents[1] / "src" / "nexus" / "dashboard_app.py"


def test_initialized_empty_runtime_is_seedable(tmp_path: Path) -> None:
    data_dir = tmp_path / "fresh"
    initialize_runtime(data_dir)
    assert runtime_is_seedable(data_dir) is True
    result = create_demo_runtime(data_dir)
    assert result["ok"] is True
    assert runtime_is_seedable(data_dir) is False


def test_practice_week_next_action_skips_holiday_and_uses_history(tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    create_demo_runtime(data_dir, now=now)

    payload = build_context(data_dir=data_dir, now=now)
    next_action = payload["next_action"]
    assert next_action["available"] is True
    assert next_action["task_id"] == "demo:homework-1"
    assert "Valentine" not in (next_action["title"] or "")
    assert "截止" in (next_action["deadline"] or "")
    assert next_action["suggestion"]
    saved = json.loads((data_dir / "judgment.json").read_text(encoding="utf-8"))
    assert saved["task_id"] == "demo:homework-1"


def test_start_complete_advances_to_lab_and_records_effort(tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    now = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    create_demo_runtime(data_dir, now=now)

    started = now + timedelta(minutes=5)
    done_at = started + timedelta(hours=1, minutes=30)
    start_task(data_dir, "demo:homework-1", now=started)
    marked = mark_task_done(data_dir, "demo:homework-1", now=done_at)
    nxt = persist_next_action(data_dir=data_dir, now=done_at)

    assert marked is not None
    assert marked.status == "done"
    entry = load_completions(data_dir)["demo:homework-1"]
    assert entry["effort_minutes"] == 90
    assert entry["effort_key"] == "assignment"
    assert nxt["available"] is True
    assert nxt["task_id"] != "demo:homework-1"
    assert nxt["deadline"]

    later = build_judgment(
        load_tasks(data_dir / "tasks.json"),
        done_ids=completed_task_ids(load_completions(data_dir)),
        now=done_at,
        completions=load_completions(data_dir),
    )
    assert later.anchor_task_id != "demo:homework-1"
    assert later.anchor_task_id != "demo:holiday"


def test_nexus_next_cli_matches_persisted_judgment(tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    create_demo_runtime(data_dir)
    code = next_cli.main(["--data-dir", str(data_dir), "--json"])
    assert code == 0
    saved = json.loads((data_dir / "judgment.json").read_text(encoding="utf-8"))
    assert saved["available"] is True
    assert saved["task_id"] == "demo:homework-1"


def test_dashboard_complete_advances_home_next_action(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert any(tab.label == "Today" for tab in app.tabs)
    assert any("截止" in item.value for item in app.markdown)
    assert any(button.label == "开始做" for button in app.button)

    next(button for button in app.button if button.label == "开始做").click()
    app.run(timeout=30)
    assert any("计时中" in item.value for item in app.markdown)

    next(button for button in app.button if button.label == "完成").click()
    app.run(timeout=30)

    assert not app.exception
    titles = [item.value for item in app.markdown]
    assert any("Lab" in value or "project" in value or "截止" in value for value in titles)
    done = next(task for task in load_tasks(data_dir / "tasks.json") if task.id == "demo:homework-1")
    assert done.status == "done"
    judgment = json.loads((data_dir / "judgment.json").read_text(encoding="utf-8"))
    assert judgment["task_id"] != "demo:homework-1"


def test_dashboard_week_blocks_each_have_start(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert any("这周你还有" in item.value for item in app.markdown)
    starts = [button for button in app.button if button.label == "开始做"]
    dones = [button for button in app.button if button.label == "完成"]
    assert len(starts) >= 2
    assert len(starts) == len(dones)


def test_tonight_and_tasks_share_open_obligations(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Today", "State", "Setup"]
    assert not any(button.label == "Add work block" for button in app.button)
    open_tasks = tasks_from_obligations(data_dir, load_tasks(data_dir / "tasks.json"))
    titles = [task.title for task in open_tasks if task.status != "done"]
    assert titles
    markdown = " ".join(item.value for item in app.markdown)
    for title in titles:
        assert title in markdown
    starts = [button for button in app.button if button.label == "开始做"]
    assert len(starts) >= len(titles)


def test_accept_today_does_not_start_timer(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "week"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    app = AppTest.from_file(APP_PATH).run(timeout=30)
    next(button for button in app.button if button.label == "接受").click()
    app.run(timeout=30)

    assert not app.exception
    assert any("已接受" in item.value for item in app.caption)
    assert not any("计时中" in item.value for item in app.markdown)
    events = ActionService(data_dir).store.list_action_events()
    accepted = [item for item in events if item.get("event_type") == "accepted"]
    assert accepted
    oid = str(accepted[0]["obligation_id"])
    row = ActionService(data_dir).store.get_obligation(oid)
    task_id = None if row is None else row.get("primary_task_id")
    assert task_id
    assert not load_completions(data_dir).get(task_id, {}).get("started_at")
