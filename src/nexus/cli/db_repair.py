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
    parser.add_argument(
        "--quarantine-concepts", action="store_true",
        help="Detect and isolate pseudo-concepts and orphan concepts",
    )
    parser.add_argument(
        "--categories", nargs="+", default=None,
        help="Quarantine categories: pseudo, orphan_no_def, orphan_with_def (default: pseudo,orphan_no_def)",
    )
    parser.add_argument(
        "--restore", type=str, default=None, metavar="CONCEPT_ID",
        help="Restore a quarantined concept back to the concepts table",
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_path)
    chroma_path = Path(args.db_dir)

    if args.migrate_metadata:
        from nexus.knowledge.sqlite_store import SQLiteStore
        from nexus.knowledge.chunk_backfill import MetadataMigration

        if args.apply:
            SQLiteStore(sqlite_path)

        migration = MetadataMigration(sqlite_path, chroma_path)
        report = migration.safe_apply() if args.apply else migration.plan()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if args.quarantine_concepts or args.restore:
        from nexus.knowledge.chunk_backfill import ConceptQuarantine

        q = ConceptQuarantine(sqlite_path, chroma_path)

        if args.restore:
            report = q.safe_restore(args.restore)
        elif args.apply:
            categories = args.categories if args.categories else None
            report = q.safe_apply(categories=categories)
        else:
            report = q.plan()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    from nexus.knowledge.chunk_backfill import ConsistencyRepairMigration

    migration = ConsistencyRepairMigration(sqlite_path, chroma_path)
    report = migration.safe_apply() if args.apply else migration.plan()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
