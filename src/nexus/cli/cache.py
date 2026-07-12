"""Inspect and clear versioned document parser cache entries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the Nexus document parser cache")
    parser.add_argument("--db-path", type=Path, help="Override the Nexus SQLite path")
    subparsers = parser.add_subparsers(dest="action", required=True)

    list_parser = subparsers.add_parser("list", help="List cache metadata")
    list_parser.add_argument("--course", help="Only list entries for one course")
    list_parser.add_argument("--json", action="store_true", dest="as_json")

    clear_parser = subparsers.add_parser("clear", help="Delete cache entries")
    clear_parser.add_argument("--course", help="Only clear entries for one course")
    clear_parser.add_argument(
        "--all",
        action="store_true",
        help="Clear every parser cache entry (required without --course)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from nexus.knowledge.sqlite_store import SQLiteStore, default_db_path

    store = SQLiteStore(args.db_path or default_db_path())
    if args.action == "list":
        entries = store.list_parse_cache(args.course)
        if args.as_json:
            print(json.dumps(entries, ensure_ascii=False, indent=2))
        elif not entries:
            print("Parser cache is empty.")
        else:
            for entry in entries:
                print(
                    f"{entry['file_hash'][:12]}  {entry['course_id'] or '-':<16} "
                    f"{entry['parse_method']:<20} {entry['model_version']} "
                    f"({entry['chunk_count']} chunks)"
                )
        return 0

    if not args.course and not args.all:
        raise SystemExit("cache clear requires --course COURSE_ID or --all")
    count = store.clear_parse_cache(args.course)
    print(f"Cleared {count} parser cache entr{'y' if count == 1 else 'ies'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
