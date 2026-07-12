"""Launch the primary Streamlit application."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli as streamlit_cli

    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    sys.argv = ["streamlit", "run", str(app_path), *sys.argv[1:]]
    streamlit_cli.main()


if __name__ == "__main__":
    main()
