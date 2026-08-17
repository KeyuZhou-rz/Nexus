from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from nexus.demo import create_demo_runtime
from nexus.storage import load_tasks


APP_PATH = Path(__file__).parents[1] / "src" / "nexus" / "dashboard_app.py"


def test_information_dashboard_renders_complete_demo_runtime(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    app = AppTest.from_file(APP_PATH).run(timeout=30)

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Tonight", "Tasks", "Setup"]
    assert {button.label for button in app.button} == {
        "Add task", "Save source", "Sync", "开始做", "完成",
    }
    assert not any(button.label == "Add work block" for button in app.button)
    assert any("截止" in markdown.value for markdown in app.markdown)
    assert any("本周日程" in markdown.value for markdown in app.markdown)
    assert any("Overdue" in markdown.value or "Today" in markdown.value or "Tomorrow" in markdown.value for markdown in app.markdown)


def test_dashboard_shows_schedule_module_when_no_deadlines(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert any("本周日程" in markdown.value for markdown in app.markdown)


def test_dashboard_manual_task_form_writes_user_owned_task(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app = AppTest.from_file(APP_PATH).run(timeout=30)

    next(item for item in app.text_input if item.label == "Title").input("Read chapter 7")
    next(item for item in app.text_input if item.label == "Course").input("CS202_OS")
    next(item for item in app.checkbox if item.label == "Set a deadline").uncheck()
    next(item for item in app.button if item.label == "Add task").click()
    app.run(timeout=30)

    manual = [
        task
        for task in load_tasks(data_dir / "tasks.json")
        if task.source == "manual" and task.id.startswith("manual:")
    ]
    assert len(manual) == 1
    assert manual[0].title == "Read chapter 7"
    assert manual[0].course == "CS202_OS"
    assert not app.exception


def test_dashboard_sync_button_uses_shared_sync_service(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": {"task_count": 3}}

    monkeypatch.setattr("nexus.dashboard.app.run_sync", fake_sync)
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    next(item for item in app.button if item.label == "Sync").click()
    app.run(timeout=30)

    assert calls == [{"data_dir": data_dir, "trigger": "dashboard", "include_google": False}]


def test_dashboard_setup_syncs_feed_without_google(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": {"task_count": 4}}

    monkeypatch.setattr("nexus.dashboard.app.run_sync", fake_sync)
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    next(item for item in app.text_input if item.label == "Name").input("Circuits")
    next(item for item in app.text_input if item.label == "Feed URL").input("https://example.edu/cal.ics")
    next(item for item in app.button if item.label == "Save source").click()
    app.run(timeout=30)

    assert calls == [{"data_dir": data_dir, "trigger": "setup", "include_google": False}]
    assert any("tonight" in success.value.lower() for success in app.success)
