"""Create an isolated synthetic Nexus runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..demo import create_demo_runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a synthetic Nexus demo runtime")
    parser.add_argument("--data-dir", type=Path, required=True, help="Empty destination directory")
    parser.add_argument("--force", action="store_true", help="Replace an existing demo runtime")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default output format)")
    args = parser.parse_args(argv)

    try:
        payload = create_demo_runtime(args.data_dir, force=args.force)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
