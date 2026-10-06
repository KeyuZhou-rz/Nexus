"""Safe, repeatable first-run setup for the local Nexus runtime."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from typing import Any, Callable

from .config import ensure_private_runtime_dir
from .io_utils import atomic_write_json
from .models import FeedSource
from .storage import save_feeds


SUPPORTED_FEED_KINDS = {"brightspace_ical", "brightspace_rss", "ical_file"}


def initialize_runtime(data_dir: Path) -> dict[str, object]:
    """Create missing runtime snapshots without replacing existing data."""
    root = ensure_private_runtime_dir(data_dir)
    created: list[str] = []
    existing: list[str] = []
    warnings: list[str] = []

    for name in ("feeds.json", "tasks.json"):
        path = root / name
        if path.exists():
            existing.append(name)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                warnings.append(f"{name} exists but is not valid JSON")
            else:
                if not isinstance(payload, list):
                    warnings.append(f"{name} exists but does not contain a JSON list")
            continue
        atomic_write_json(path, [])
        created.append(name)

    return {
        "data_dir": str(root),
        "created": created,
        "existing": existing,
        "warnings": warnings,
        "ready": not warnings,
    }


def validate_feed(feed: FeedSource) -> None:
    if feed.kind not in SUPPORTED_FEED_KINDS:
        raise ValueError(f"Unsupported feed kind: {feed.kind}")
    if not feed.name.strip():
        raise ValueError("Feed name is required")
    if not feed.url.strip():
        raise ValueError("Feed URL or file path is required")

    parsed = urlparse(feed.url.strip())
    if parsed.scheme in {"http", "ftp"}:
        raise ValueError("Remote feeds must use HTTPS")
    if feed.kind == "brightspace_rss" and parsed.scheme != "https":
        raise ValueError("Brightspace RSS feeds must use HTTPS")
    if parsed.scheme and parsed.scheme not in {"https", "file"}:
        raise ValueError(f"Unsupported feed URL scheme: {parsed.scheme}")


def upsert_feed(data_dir: Path, feed: FeedSource) -> dict[str, object]:
    """Add or update one feed while preserving all unrelated entries."""
    validate_feed(feed)
    root = Path(data_dir).expanduser()
    setup = initialize_runtime(root)
    if setup["warnings"]:
        raise ValueError("Cannot update feeds.json until its existing JSON is repaired")

    feeds_path = root / "feeds.json"
    payload = json.loads(feeds_path.read_text(encoding="utf-8"))
    feeds = [FeedSource.from_dict(item) for item in payload if isinstance(item, dict)]
    normalized_url = feed.url.strip()
    normalized = FeedSource(
        kind=feed.kind,
        name=feed.name.strip(),
        url=normalized_url,
        course=feed.course.strip() if feed.course and feed.course.strip() else None,
        enabled=feed.enabled,
        audience=feed.audience,
        mode=feed.mode,
    )

    action = "created"
    for index, current in enumerate(feeds):
        if current.url.strip() == normalized_url:
            feeds[index] = normalized
            action = "updated"
            break
    else:
        feeds.append(normalized)

    save_feeds(feeds, feeds_path)
    return {
        "action": action,
        "feed": normalized.to_dict(),
        "feed_count": len(feeds),
        "feeds_path": str(feeds_path),
    }


def connect_calendar_feed(
    data_dir: Path,
    feed: FeedSource,
    *,
    sync: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Save one calendar/announcement feed and immediately refresh tasks.

    First-run sync stays off Google so holiday calendars and inbox noise
    cannot drown the LMS deadlines the student just connected.
    """
    saved = upsert_feed(data_dir, feed)
    receipt = sync(data_dir=data_dir, trigger="setup", include_google=False)
    return {"feed": saved, "sync": receipt}
