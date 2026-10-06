"""Initialize a local Nexus runtime and optionally configure an LMS feed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.config import default_config
from ..core.models import FeedSource
from ..core.onboarding import initialize_runtime, upsert_feed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize a local Nexus data directory")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--feed-url", help="Add or update a Brightspace feed")
    parser.add_argument(
        "--feed-kind",
        choices=("brightspace_ical", "brightspace_rss", "ical_file"),
        default="brightspace_ical",
    )
    parser.add_argument("--feed-name", help="Display name for the feed")
    parser.add_argument("--course", help="Course identifier associated with the feed")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default output format)")
    args = parser.parse_args(argv)

    root = args.data_dir or default_config().data_dir
    payload: dict[str, object] = {"schema_version": "1.0"}
    payload["runtime"] = initialize_runtime(root)
    if args.feed_url:
        feed = FeedSource(
            kind=args.feed_kind,
            name=args.feed_name or args.course or "Brightspace",
            url=args.feed_url,
            course=args.course,
        )
        try:
            payload["feed"] = upsert_feed(root, feed)
        except ValueError as exc:
            payload["ok"] = False
            payload["error"] = str(exc)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
    payload["ok"] = bool(payload["runtime"]["ready"])
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
