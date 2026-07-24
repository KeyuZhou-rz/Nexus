"""Read-only operational status command for schedulers and operators."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show durable Nexus runtime status without modifying data")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--db-path", type=Path, help="Override the SQLite path used for session backlog")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default output format)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from nexus.config import default_config
    from nexus.runtime_status import build_runtime_status

    data_dir = args.data_dir or default_config().data_dir
    print(json.dumps(build_runtime_status(data_dir, db_path=args.db_path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
