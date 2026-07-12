"""Scheduler-friendly session finalization commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize idle Nexus learning sessions")
    parser.add_argument("--db-path", type=Path, help="Override the Nexus SQLite path")
    subparsers = parser.add_subparsers(dest="action", required=True)
    finalize = subparsers.add_parser("finalize-idle", help="Finalize sessions past idle timeout")
    finalize.add_argument("--idle-minutes", type=int, default=5)
    finalize.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from nexus.config import default_config
    from nexus.knowledge.session_manager import SessionManager
    from nexus.knowledge.sqlite_store import SQLiteStore
    from nexus.knowledge.user_profile import UserProfileManager

    db_path = args.db_path or (default_config().data_dir / "nexus.db")
    store = SQLiteStore(db_path)
    manager = SessionManager(
        sqlite_store=store,
        profile_manager=UserProfileManager(store),
        idle_timeout_minutes=args.idle_minutes,
    )
    ended = manager.check_and_end_idle_sessions()
    payload = {"status": "ok", "finalized": ended, "count": len(ended)}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Finalized {len(ended)} idle session(s).")
        for session_id in ended:
            print(f"  {session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
