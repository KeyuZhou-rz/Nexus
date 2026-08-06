from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from nexus.demo import create_demo_runtime


APP_PATH = Path(__file__).parents[1] / "src" / "nexus" / "dashboard_app.py"


def test_information_dashboard_renders_complete_demo_runtime(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"
    create_demo_runtime(data_dir)
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")

    app = AppTest.from_file(APP_PATH).run(timeout=30)

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Home", "Tasks", "Status", "Setup"]
    assert {button.label for button in app.button} == {"Initialize local data", "Save source"}
