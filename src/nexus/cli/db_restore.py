from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or restore a Nexus database backup")
    parser.add_argument("archive")
    parser.add_argument("--sqlite-path", default="data/nexus.db")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--apply", action="store_true", help="Apply the restore; default is validation only")
    parser.add_argument("--force", action="store_true", help="Replace existing targets (requires --apply)")
    args = parser.parse_args()
    if args.force and not args.apply:
        parser.error("--force requires --apply")

    from nexus.knowledge.maintenance import restore_backup

    report = restore_backup(
        Path(args.archive), Path(args.sqlite_path), Path(args.db_dir), apply=args.apply, force=args.force
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
