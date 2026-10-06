"""Durable, bounded-retry queues for operational failures.

Queue files are deliberately JSON rather than a new database table: callers
can inspect and resolve them without starting Nexus, while atomic writes keep
the scheduler-safe persistence contract used by the existing archive queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json


DEFAULT_MAX_RETRIES = 5


def build_failure_report(failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failure_count": len(failures),
        "failures": failures,
    }


def save_failure_report(path: Path, failures: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, build_failure_report(failures), ensure_ascii=False)


def load_failure_queue(path: Path) -> list[dict[str, Any]]:
    """Read a queue without treating malformed operational data as a crash."""
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    failures = payload.get("failures", []) if isinstance(payload, dict) else []
    return [item for item in failures if isinstance(item, dict)] if isinstance(failures, list) else []


def failure_key(item: dict[str, Any]) -> str:
    """Return a stable identity for archive, aggregation, and ingest failures."""
    operation = str(item.get("operation") or "archive").strip()
    course = str(item.get("course") or item.get("course_id") or "").strip()
    for field in ("attachment_url", "input_path", "source"):
        value = str(item.get(field) or "").strip()
        if value:
            return f"{operation}|{course}|{field}:{value}"
    title = str(item.get("assignment_title") or item.get("title") or "").strip()
    if title:
        return f"{operation}|{course}|title:{title}"
    # Last-resort deterministic key: error text is intentionally excluded so
    # a changing exception message cannot create unbounded duplicate entries.
    stable = {
        key: value for key, value in item.items()
        if key not in {"error", "retry_count", "last_error", "last_seen_at", "first_seen_at"}
    }
    return f"{operation}|payload:{json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str)}"


def _identity_values(item: dict[str, Any]) -> set[str]:
    return {
        failure_key(item),
        *(
            str(item.get(field)).strip()
            for field in ("attachment_url", "input_path", "source")
            if str(item.get(field) or "").strip()
        ),
    }


def update_failure_queue(
    path: Path,
    new_failures: list[dict[str, Any]],
    resolved_attachment_urls: set[str] | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Merge failed work, clear resolved work, and dead-letter exhausted work.

    ``resolved_attachment_urls`` retains its legacy name for archive callers,
    but accepts generic failure keys or source/path identifiers as well.
    Items that exceed ``max_retries`` are retained in ``{path}.dead`` with an
    explicit ``manual_review_required`` marker rather than silently dropped.
    """
    existing = load_failure_queue(path)
    resolved = {str(value).strip() for value in (resolved_attachment_urls or set()) if str(value).strip()}
    incoming = {failure_key(item): dict(item) for item in new_failures if isinstance(item, dict)}
    combined: dict[str, dict[str, Any]] = {}
    dead: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()

    for item in existing:
        key = failure_key(item)
        if _identity_values(item) & resolved:
            continue
        updated = dict(item)
        if key in incoming:
            updated["retry_count"] = int(updated.get("retry_count", 0)) + 1
            updated["error"] = incoming[key].get("error", updated.get("error"))
            updated["last_error"] = updated.get("error")
            updated["last_seen_at"] = now
        else:
            updated["retry_count"] = int(updated.get("retry_count", 0))
        if updated["retry_count"] > max_retries:
            updated["manual_review_required"] = True
            dead.append(updated)
        else:
            combined[key] = updated

    dead_keys = {failure_key(item) for item in dead}
    for key, item in incoming.items():
        if key in combined or key in dead_keys:
            continue
        item.setdefault("retry_count", 0)
        item.setdefault("first_seen_at", now)
        item.setdefault("last_seen_at", now)
        combined[key] = item

    failures = list(combined.values())
    save_failure_report(path, failures)

    if dead:
        dead_path = Path(f"{path}.dead")
        existing_dead = load_failure_queue(dead_path)
        known_keys = {failure_key(item) for item in existing_dead}
        existing_dead.extend(item for item in dead if failure_key(item) not in known_keys)
        save_failure_report(dead_path, existing_dead)

    report = build_failure_report(failures)
    report["dead_letter_count"] = len(load_failure_queue(Path(f"{path}.dead")))
    report["manual_review_required"] = report["dead_letter_count"] > 0
    return report
