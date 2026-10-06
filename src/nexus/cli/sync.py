"""Run one safe, non-interactive synchronization cycle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.sync_service import run_sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a single-flight Nexus sync suitable for terminals and schedulers",
    )
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument(
        "--include-archive",
        action="store_true",
        help="Also run advanced browser-based Brightspace archive sync",
    )
    parser.add_argument("--archive-timeout", type=int, default=420)
    parser.add_argument(
        "--google",
        action="store_true",
        help="Also fetch Gmail and Calendar (off by default)",
    )
    parser.add_argument(
        "--no-google",
        action="store_true",
        help="Skip Gmail and Calendar (default; kept for existing scripts)",
    )
    args = parser.parse_args(argv)

    try:
        receipt = run_sync(
            data_dir=args.data_dir,
            trigger="cli",
            include_archive=args.include_archive,
            include_google=bool(args.google) and not args.no_google,
            archive_timeout=args.archive_timeout,
        )
    except Exception as exc:
        receipt = {
            "schema_version": "1.0",
            "ok": False,
            "status": "failed",
            "trigger": "cli",
            "error_type": type(exc).__name__,
        }
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    if receipt.get("status") == "busy":
        return 2
    return 0 if receipt.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
