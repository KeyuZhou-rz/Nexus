from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a checksummed SQLite and Chroma backup")
    parser.add_argument("--sqlite-path", default="data/nexus.db")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--output", default=f"data/backups/nexus-{datetime.now():%Y%m%d-%H%M%S}.zip")
    parser.add_argument("--force", action="store_true", help="Replace an existing backup archive")
    args = parser.parse_args()

    from nexus.knowledge.maintenance import create_backup

    report = create_backup(Path(args.sqlite_path), Path(args.db_dir), Path(args.output), force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
