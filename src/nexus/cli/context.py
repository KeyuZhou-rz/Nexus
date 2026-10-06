"""Print the canonical Nexus context document."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.config import default_config
from ..core.context_api import build_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the current Nexus learning context")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--course-id", help="Limit tasks and learning state to one course")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default output format)")
    args = parser.parse_args(argv)
    data_dir = args.data_dir or default_config().data_dir
    payload = build_context(data_dir=data_dir, course_id=args.course_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
