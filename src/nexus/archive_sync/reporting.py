from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from ..io_utils import atomic_write_json


def build_failure_report(failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "failure_count": len(failures),
        "failures": failures,
    }


def save_failure_report(path: Path, failures: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, build_failure_report(failures), ensure_ascii=False)


def load_failure_queue(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    failures = payload.get("failures", [])
    if not isinstance(failures, list):
        return []
    return [item for item in failures if isinstance(item, dict)]


def _failure_key(item: dict[str, Any]) -> str:
    attachment_url = str(item.get("attachment_url", "")).strip()
    if attachment_url:
        return attachment_url
    course = str(item.get("course", "")).strip()
    title = str(item.get("assignment_title", "")).strip()
    return f"{course}|{title}"


DEFAULT_MAX_RETRIES = 5


def update_failure_queue(
    path: Path,
    new_failures: list[dict[str, Any]],
    resolved_attachment_urls: set[str] | None = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """Merge new failures into the queue, preserving retry counts.

    Phase 4E-4: failures exceeding ``max_retries`` are moved to the
    dead-letter queue at ``{path}.dead`` instead of being dropped silently.
    """
    existing = load_failure_queue(path)
    resolved = {url for url in (resolved_attachment_urls or set()) if url}
    combined: dict[str, dict[str, Any]] = {}
    dead: list[dict[str, Any]] = []

    for item in existing:
        key = _failure_key(item)
        if not key:
            continue
        if key in resolved:
            continue
        retry_count = int(item.get("retry_count", 0))
        # If the item appeared in new_failures again, bump its retry count
        if any(_failure_key(nf) == key for nf in new_failures):
            retry_count += 1
        item["retry_count"] = retry_count
        if retry_count > max_retries:
            dead.append(item)
        else:
            combined[key] = item

    for item in new_failures:
        key = _failure_key(item)
        if not key:
            continue
        if key not in combined and not any(d.get("attachment_url") == item.get("attachment_url") for d in dead):
            item["retry_count"] = item.get("retry_count", 0)
            combined[key] = item

    failures = list(combined.values())
    report = build_failure_report(failures)
    report["dead_letter_count"] = len(dead)
    save_failure_report(path, failures)

    # Write dead-letter queue
    if dead:
        dead_path = Path(str(path) + ".dead")
        dead_path.parent.mkdir(parents=True, exist_ok=True)
        existing_dead = load_failure_queue(dead_path)
        dead_keys = {_failure_key(d) for d in existing_dead}
        for d in dead:
            if _failure_key(d) not in dead_keys:
                existing_dead.append(d)
        save_failure_report(dead_path, existing_dead)

    return report
