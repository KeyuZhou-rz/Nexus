from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair deterministic index drift and migrate metadata")
    parser.add_argument("--sqlite-path", default="data/nexus.db")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--apply", action="store_true", help="Apply safe repairs or migration; default is dry-run")
    parser.add_argument(
        "--migrate-metadata", action="store_true",
        help="Upgrade legacy Chroma metadata to canonical contract (MD5→SHA-256, chunk_index, etc.)",
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path)
    chroma_path = Path(args.db_dir)

    if args.migrate_metadata:
        from nexus.knowledge.sqlite_store import SQLiteStore
        from nexus.knowledge.chunk_backfill import MetadataMigration

        # Ensure schema migrations are applied (v6 adds chunk_index column)
        if args.apply:
            SQLiteStore(sqlite_path)

        migration = MetadataMigration(sqlite_path, chroma_path)
        if args.apply:
            report = migration.apply()
        else:
            report = migration.plan()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    from nexus.knowledge.maintenance import repair_consistency

    report = repair_consistency(sqlite_path, chroma_path, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
