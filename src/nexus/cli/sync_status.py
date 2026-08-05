"""Show normalized source synchronization health without running sync."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show read-only Brightspace, Google, and Canvas sync status",
    )
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default output format)")
    args = parser.parse_args(argv)

    from nexus.config import default_config
    from nexus.sync_control import build_sync_status

    config = default_config()
    root = args.data_dir or config.data_dir
    if args.data_dir is not None:
        config.data_dir = root
        config.feeds_path = root / "feeds.json"
        if not os.getenv("NEXUS_GOOGLE_CREDENTIALS"):
            config.google_credentials_path = root / "google_client_secret.json"
        if not os.getenv("NEXUS_GOOGLE_TOKEN"):
            config.google_token_path = root / "google_token.json"
    now = datetime.now(timezone.utc)
    sources = build_sync_status(root, config=config, now=now)
    failed = sum(1 for source in sources.values() if source["status"] == "failed")
    payload = {
        "schema_version": "1.0",
        "generated_at": now.isoformat(),
        "ok": failed == 0,
        "failed_sources": failed,
        "sources": sources,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
