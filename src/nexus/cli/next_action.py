"""Print the single next action for tonight."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..config import default_config
from ..context_api import persist_next_action


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print tonight's next action")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the Next Action JSON")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir or default_config().data_dir).expanduser()
    payload = persist_next_action(data_dir=data_dir)

    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not payload.get("available"):
        print("还没有截止事项。把 Brightspace 日历贴到 Setup，或运行 nexus demo --data-dir <空目录>。")
        return 0

    print(payload.get("deadline") or payload.get("suggestion"))
    if payload.get("pace"):
        print()
        print(payload["pace"])
    upcoming = payload.get("upcoming") or []
    if upcoming:
        print()
        print("这周你还有：")
        for line in upcoming:
            print(line)
    if payload.get("forecast"):
        print()
        print(payload["forecast"])
    if payload.get("suggestion"):
        print()
        print("建议：")
        print(payload["suggestion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
