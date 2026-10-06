"""Read-only operational status assembled from Nexus durable artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .failure_queue import load_failure_queue


def _load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"available": False, "path": str(path), "error": f"Unreadable report: {exc}"}
    if not isinstance(payload, dict):
        return {"available": False, "path": str(path), "error": "Report is not a JSON object"}
    return {"available": True, "path": str(path), **payload}


def _session_backlog(db_path: Path) -> dict[str, Any]:
    """Summarize retry/manual-review session rows without initializing SQLite."""
    if not db_path.is_file():
        return {"available": False, "db_path": str(db_path), "by_status": {}, "count": 0}
    try:
        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(
                """SELECT COALESCE(finalize_status, 'active') AS status, COUNT(*) AS count
                   FROM active_sessions
                   GROUP BY COALESCE(finalize_status, 'active')"""
            ).fetchall()
    except sqlite3.Error as exc:
        return {"available": False, "db_path": str(db_path), "by_status": {}, "count": 0, "error": str(exc)}
    by_status = {str(status): int(count) for status, count in rows}
    return {
        "available": True,
        "db_path": str(db_path),
        "by_status": by_status,
        "count": sum(by_status.values()),
        "manual_review_count": by_status.get("abandoned", 0),
    }


def _queue_status(path: Path) -> dict[str, Any]:
    pending = load_failure_queue(path)
    manual_review = load_failure_queue(Path(f"{path}.dead"))
    return {
        "path": str(path),
        "pending_count": len(pending),
        "manual_review_count": len(manual_review),
    }


def build_runtime_status(data_dir: Path, *, db_path: Path | None = None) -> dict[str, Any]:
    """Return a read-only status document suitable for CLI, API, or dashboards."""
    data_dir = Path(data_dir)
    database = db_path or data_dir / "nexus.db"
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "pipeline": _load_report(data_dir / "pipeline_report.json"),
        "sync": _load_sync_receipt(data_dir / "sync_receipt.json"),
        "session_finalization": _load_report(data_dir / "session_finalization_report.json"),
        "backup": _load_report(data_dir / "backup_report.json"),
        "session_backlog": _session_backlog(database),
        "failure_queues": {
            "archive": _queue_status(data_dir / "archive_failures.json"),
            "aggregation": _queue_status(data_dir / "aggregation_failures.json"),
            "ingest": _queue_status(data_dir / "ingest_failures.json"),
        },
    }


def _load_sync_receipt(path: Path) -> dict[str, Any]:
    payload = _load_report(path)
    if not payload.get("available"):
        return payload
    sources = payload.get("sources")
    if isinstance(sources, dict):
        allowed = {
            "source", "enabled", "configured", "status", "freshness",
            "last_success_at", "item_count",
        }
        payload["sources"] = {
            str(name): {
                key: value.get(key)
                for key in allowed
                if isinstance(value, dict) and key in value
            }
            for name, value in sources.items()
            if isinstance(value, dict)
        }
    return payload
