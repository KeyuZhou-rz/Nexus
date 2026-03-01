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


def update_failure_queue(
    path: Path,
    new_failures: list[dict[str, Any]],
    resolved_attachment_urls: set[str] | None = None,
) -> dict[str, Any]:
    existing = load_failure_queue(path)
    resolved = {url for url in (resolved_attachment_urls or set()) if url}
    combined: dict[str, dict[str, Any]] = {}

    for item in existing:
        key = _failure_key(item)
        if key and key not in resolved:
            combined[key] = item

    for item in new_failures:
        key = _failure_key(item)
        if key:
            combined[key] = item

    failures = list(combined.values())
    report = build_failure_report(failures)
    save_failure_report(path, failures)
    return report
