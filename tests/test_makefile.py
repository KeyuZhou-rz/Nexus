from __future__ import annotations

from pathlib import Path


MAKEFILE = Path(__file__).parents[1] / "Makefile"


def test_streamlit_targets_use_project_python() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "$(PYTHON) -m streamlit run src/nexus/dashboard_app.py" in makefile
    assert "streamlit_app.py" not in makefile
