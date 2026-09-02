"""Today surface: one next action from build_today, no priority picker."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from nexus.academic_state_service import AcademicStateService
from nexus.v1_config import V1_NOW

from tests.fixtures.v1_loop import CS_TITLE, cs_due_original, twelve_hour_rules


APP_PATH = Path(__file__).parents[1] / "src" / "nexus" / "dashboard_app.py"


def test_dashboard_today_renders_primary_cs_assignment(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "today"
    data_dir.mkdir()
    state = AcademicStateService(data_dir)
    state.add_manual_obligation(
        title=CS_TITLE,
        course="CS101",
        due_at=cs_due_original(),
        estimated_hours=6,
        remaining_hours=6,
        kind="assignment",
        now=V1_NOW,
    )
    for rule in twelve_hour_rules():
        state.add_availability_rule(**rule)

    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app = AppTest.from_file(APP_PATH).run(timeout=30)

    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Today", "State", "Setup"]
    markdown = " ".join(item.value for item in app.markdown)
    assert CS_TITLE in markdown
    labels = [button.label for button in app.button]
    assert "开始做" in labels
    assert "完成" in labels
    assert not any(item.label == "Priority" for item in app.selectbox)
