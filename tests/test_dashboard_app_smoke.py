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
    assert [tab.label for tab in app.tabs] == ["Today", "State", "Setup"]
    labels = {button.label for button in app.button}
    assert {"加上功课", "保存来源", "更新", "开始做", "完成", "接受"}.issubset(labels)
    assert "Sync" not in labels
    assert "Add task" not in labels
    assert not any(item.label == "Priority" for item in app.selectbox)
    assert not any(button.label == "Add work block" for button in app.button)
    markdown = " ".join(item.value for item in app.markdown)
    assert "remaining_exceeds_available" not in markdown
    assert "今日" in markdown or "刷新于" in markdown
    assert any(exp.label == "本周日程" for exp in app.expander)


def test_dashboard_shows_schedule_module_when_no_deadlines(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert any(exp.label == "本周日程" for exp in app.expander)


def test_dashboard_manual_task_form_writes_user_owned_task(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app = AppTest.from_file(APP_PATH).run(timeout=30)

    next(item for item in app.text_input if item.label == "作业标题").input("Read chapter 7")
    next(item for item in app.text_input if item.label == "课程").input("CS202_OS")
    next(item for item in app.checkbox if item.label == "设截止日期").uncheck()
    next(item for item in app.button if item.label == "加上功课").click()
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
    next(item for item in app.button if item.label == "更新").click()
    app.run(timeout=30)

    assert calls == [{"data_dir": data_dir, "trigger": "dashboard", "include_google": False}]
    assert any("已更新" in item.value for item in app.caption)


def test_dashboard_sync_includes_google_when_authorized(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    (data_dir / "google_client_secret.json").write_text("{}", encoding="utf-8")
    (data_dir / "google_token.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": {"task_count": 3}}

    monkeypatch.setattr("nexus.dashboard.app.run_sync", fake_sync)
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    next(item for item in app.button if item.label == "更新").click()
    app.run(timeout=30)

    assert calls == [{"data_dir": data_dir, "trigger": "dashboard", "include_google": True}]


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
    next(item for item in app.text_input if item.label == "名称").input("Circuits")
    next(item for item in app.text_input if item.label == "订阅链接").input("https://example.edu/cal.ics")
    next(item for item in app.button if item.label == "保存来源").click()
    app.run(timeout=30)

    assert calls == [{"data_dir": data_dir, "trigger": "setup", "include_google": False}]
    assert any("today" in success.value.lower() for success in app.success)
