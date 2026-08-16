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
    assert [tab.label for tab in app.tabs] == ["Home", "Tasks", "Status", "Setup"]
    assert {button.label for button in app.button} == {
        "Add task", "Initialize local data", "Save source", "Sync now",
        "Add work block", "Mark done",
    }
    assert any("Learning Focus" in markdown.value for markdown in app.markdown)


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
    next(item for item in app.button if item.label == "Sync now").click()
    app.run(timeout=30)

    assert calls == [{"data_dir": data_dir, "trigger": "dashboard"}]
    assert any("Sync complete" in success.value for success in app.success)
