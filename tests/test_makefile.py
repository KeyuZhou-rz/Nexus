from __future__ import annotations

from pathlib import Path


MAKEFILE = Path(__file__).parents[1] / "Makefile"


def test_streamlit_targets_use_project_python() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "$(PYTHON) -m streamlit run src/nexus/core/dashboard_app.py" in makefile
    assert "streamlit_app.py" not in makefile


def test_v1_acceptance_uses_temporary_directory() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "v1-acceptance:" in makefile
    assert "mktemp -d /tmp/nexus-v1-XXXXXX" in makefile
    assert "--scenario v1-loop" in makefile
