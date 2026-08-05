"""Read model for source synchronization health and freshness."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .config import AppConfig, default_config
from .failure_queue import load_failure_queue
from .models import FeedSource
from .storage import load_feeds, load_tasks


@dataclass(frozen=True)
class SourceSyncStatus:
    source: str
    enabled: bool
    configured: bool
    status: str
    freshness: str
    last_success_at: str | None
    item_count: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "enabled": self.enabled,
            "configured": self.configured,
            "status": self.status,
            "freshness": self.freshness,
            "last_success_at": self.last_success_at,
            "item_count": self.item_count,
            "error": self.error,
        }


def _read_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _raw_status(report: dict[str, Any], source: str) -> str | None:
    """Read source status from legacy top-level or current pipeline reports."""
    sources = report.get("sources")
    if isinstance(sources, dict) and source in sources:
        value = sources.get(source)
        return str(value) if value is not None else None

    # ``run_mvp_pipeline`` stores per-source status under the aggregation
    # step. Keep the reader tolerant of older top-level snapshots as well.
    steps = report.get("steps")
    if isinstance(steps, list):
        for step in reversed(steps):
            if not isinstance(step, dict) or step.get("name") != "aggregation":
                continue
            details = step.get("details")
            if not isinstance(details, dict):
                continue
            step_sources = details.get("sources")
            if isinstance(step_sources, dict) and source in step_sources:
                value = step_sources.get(source)
                return str(value) if value is not None else None
    return None


def _configured_sources(config: AppConfig, feeds: list[FeedSource]) -> dict[str, bool]:
    brightspace = any(
        feed.enabled and bool(feed.url.strip())
        for feed in feeds
        if feed.kind in {"brightspace_ical", "brightspace_rss", "ical_file"}
    )
    google = bool(
        config.google_credentials_path
        and config.google_token_path
        and config.google_credentials_path.exists()
        and config.google_token_path.exists()
    )
    canvas = bool(config.canvas_url and config.canvas_token)
    return {"brightspace": brightspace, "google": google, "canvas": canvas}


def _failure_for_source(data_dir: Path, source: str) -> str | None:
    for item in load_failure_queue(data_dir / "aggregation_failures.json"):
        if str(item.get("source") or "") == source:
            error = str(item.get("error") or "").strip()
            if error:
                return error
    return None


def _status_value(raw: str | None, configured: bool) -> tuple[bool, str]:
    normalized = (raw or "").casefold()
    if normalized == "failed" or normalized.startswith("failed"):
        return True, "failed"
    if normalized == "disabled":
        return False, "disabled"
    if normalized == "skipped" or normalized.startswith("skipped"):
        return configured, "not_run" if configured else "disabled"
    if normalized == "succeeded" or normalized.startswith("succeeded"):
        return True, "succeeded"
    return configured, "not_run" if configured else "disabled"


def build_sync_status(
    data_dir: Path | None = None,
    *,
    config: AppConfig | None = None,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(hours=24),
) -> dict[str, dict[str, Any]]:
    """Return normalized source status keyed by source name.

    This is a read model. It derives state from existing durable reports and
    does not run network calls or alter sync artifacts.
    """
    config = config or default_config()
    root = Path(data_dir or config.data_dir).expanduser()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    feeds = load_feeds(root / "feeds.json")
    configured = _configured_sources(config, feeds)
    report = _read_report(root / "pipeline_report.json")
    run_at = _parse_timestamp(report.get("generated_at"))
    tasks = load_tasks(root / "tasks.json")
    statuses: dict[str, dict[str, Any]] = {}

    for source in ("brightspace", "google", "canvas"):
        enabled, status = _status_value(_raw_status(report, source), configured[source])
        last_success = run_at if status == "succeeded" else None
        if status == "succeeded" and last_success is not None:
            freshness = "fresh" if current - last_success <= stale_after else "stale"
        elif status == "failed":
            freshness = "stale"
        elif status == "not_run":
            freshness = "unknown"
        else:
            freshness = "disabled"
        source_aliases = {
            "brightspace": {"brightspace"},
            "google": {"google", "gcal", "gmail"},
            "canvas": {"canvas"},
        }
        item_count = sum(1 for task in tasks if task.source in source_aliases[source])
        statuses[source] = SourceSyncStatus(
            source=source,
            enabled=enabled,
            configured=configured[source],
            status=status,
            freshness=freshness,
            last_success_at=last_success.isoformat() if last_success else None,
            item_count=item_count,
            error=_failure_for_source(root, source) if status == "failed" else None,
        ).to_dict()
    return statuses
