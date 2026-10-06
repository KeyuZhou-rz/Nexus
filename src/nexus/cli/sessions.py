"""Scheduler-friendly session finalization commands."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from nexus.core.io_utils import atomic_write_json


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finalize idle Nexus learning sessions")
    parser.add_argument("--db-path", type=Path, help="Override the Nexus SQLite path")
    subparsers = parser.add_subparsers(dest="action", required=True)
    finalize = subparsers.add_parser("finalize-idle", help="Finalize sessions past idle timeout")
    finalize.add_argument("--idle-minutes", type=int, default=5)
    finalize.add_argument("--json", action="store_true", dest="as_json")
    abandon = subparsers.add_parser("abandon", help="Permanently stop retrying one session")
    abandon.add_argument("--session-id", required=True)
    abandon.add_argument("--reason", required=True)
    abandon.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from nexus.core.config import default_config
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
    if args.action == "abandon":
        payload = manager.abandon_session(args.session_id, args.reason)
        _write_status_report(db_path.parent, payload)
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"Session {args.session_id}: {payload['status']}")
        return 0 if payload["status"] != "not_found" else 1

    ended = manager.check_and_end_idle_sessions()
    payload = {"status": "ok", "finalized": ended, "count": len(ended)}
    _write_status_report(db_path.parent, payload)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Finalized {len(ended)} idle session(s).")
        for session_id in ended:
            print(f"  {session_id}")
    return 0


def _write_status_report(data_dir: Path, payload: dict[str, object]) -> None:
    """Persist the last scheduler outcome; successful sessions are later deleted from SQLite."""
    atomic_write_json(
        data_dir / "session_finalization_report.json",
        {
            "schema_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": payload.get("status") not in {"not_found"},
            **payload,
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
