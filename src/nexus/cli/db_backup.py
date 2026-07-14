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
    parser.add_argument(
        "--retain", type=int, default=0,
        help="Keep only the N most recent backups; delete older ones (0 = keep all)",
    )
    parser.add_argument(
        "--verify", type=str, default=None, metavar="BACKUP_PATH",
        help="Verify a backup archive without restoring it",
    )
    args = parser.parse_args()

    from nexus.knowledge.maintenance import create_backup, verify_backup

    if args.verify:
        report = verify_backup(Path(args.verify))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    report = create_backup(Path(args.sqlite_path), Path(args.db_dir), Path(args.output), force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Phase 4E-3: retention cleanup
    if args.retain > 0:
        _prune_backups(Path(args.output).parent, args.retain)

    return 0


def _prune_backups(backup_dir: Path, keep: int) -> None:
    """Keep only the ``keep`` most recent backup archives; remove older ones."""
    archives = sorted(backup_dir.glob("nexus-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(archives) <= keep:
        return
    for path in archives[keep:]:
        path.unlink(missing_ok=True)
        print(f"Pruned old backup: {path.name}", file=__import__("sys").stderr)


if __name__ == "__main__":
    raise SystemExit(main())
