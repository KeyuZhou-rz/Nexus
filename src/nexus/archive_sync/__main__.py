"""
Entry point: python -m nexus.archive_sync

Reads environment variables, runs the Playwright scraper, merges results
into tasks.json, then prints a JSON summary to stdout.
Debug logs go to stderr only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure src/ is on the path when invoked directly
SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nexus.archive_sync.scraper import run_scraper
from nexus.archive_sync.reporting import update_failure_queue
from nexus.models import Task
from nexus.schemas import ARCHIVE_RESULT_SCHEMA_VERSION
from nexus.storage import load_tasks, save_tasks


def _dedupe(tasks: list[Task]) -> list[Task]:
    seen: dict[str, Task] = {}
    for task in tasks:
        key = task.id or f"{task.source}:{task.title}:{task.due_at}"
        if key not in seen:
            seen[key] = task
    return list(seen.values())


def main() -> None:
    base_url = os.environ.get("NEXUS_BRIGHTSPACE_URL", "").rstrip("/")
    username = os.environ.get("NEXUS_BRIGHTSPACE_USERNAME", "")
    password = os.environ.get("NEXUS_BRIGHTSPACE_PASSWORD", "")
    archive_root = Path(
        os.environ.get(
            "NEXUS_ARCHIVE_DIR",
            str(Path(__file__).resolve().parents[3] / "data" / "archives"),
        )
    )
    failure_report_path = Path(__file__).resolve().parents[3] / "data" / "archive_failures.json"

    if not base_url or not username or not password:
        result = {
            "status": "error",
            "schema_version": ARCHIVE_RESULT_SCHEMA_VERSION,
            "tasks": [],
            "data": [],
            "message": (
                "Missing required environment variables: "
                "NEXUS_BRIGHTSPACE_URL, NEXUS_BRIGHTSPACE_USERNAME, NEXUS_BRIGHTSPACE_PASSWORD"
            ),
        }
        print(json.dumps(result))
        sys.exit(1)

    result = asyncio.run(run_scraper(base_url, username, password, archive_root=archive_root))
    result["schema_version"] = ARCHIVE_RESULT_SCHEMA_VERSION
    result["data"] = result.get("archives", [])
    result["archive_failures"] = result.get("archive_failures", [])
    resolved_urls = {
        str(item.get("attachment_url", "")).strip()
        for item in result.get("data", [])
        if isinstance(item, dict)
    }
    queue_report = update_failure_queue(
        failure_report_path,
        result["archive_failures"],
        resolved_attachment_urls=resolved_urls,
    )
    result["archive_failure_queue_count"] = queue_report.get("failure_count", 0)

    if result["status"] == "success" and result["tasks"]:
        # Convert raw dicts to Task objects
        # scraper returns due_at as datetime objects; Task.from_dict expects ISO strings
        new_tasks: list[Task] = []
        for raw in result["tasks"]:
            try:
                if hasattr(raw.get("due_at"), "isoformat"):
                    raw = {**raw, "due_at": raw["due_at"].isoformat()}
                new_tasks.append(Task.from_dict(raw))
            except Exception as exc:
                print(f"[archive_sync] Skipping malformed task: {exc}", file=sys.stderr)

        # Merge with existing tasks
        existing = load_tasks()
        merged = _dedupe([*existing, *new_tasks])
        save_tasks(merged)
        print(
            f"[archive_sync] Merged {len(new_tasks)} new tasks → {len(merged)} total in tasks.json",
            file=sys.stderr,
        )
        result["tasks_merged"] = len(merged)
        result["tasks_new"] = len(new_tasks)

    # Only JSON goes to stdout
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
