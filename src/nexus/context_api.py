"""Stable, read-only context boundary for assistants and integrations."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from .config import default_config
from .runtime_status import build_runtime_status
from .storage import load_tasks, tasks_last_updated
from .sync_control import build_sync_status


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _task_sort_key(task: Any) -> tuple[int, str]:
    due = task.due_at
    if due is None:
        return (2**63 - 1, task.id)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (int(due.timestamp()), task.id)


def _learning_context(db_path: Path, course_id: str | None) -> dict[str, Any]:
    if not db_path.is_file():
        return {"available": False, "reason": "knowledge_store_not_initialized"}

    try:
        from .knowledge.memory_api import MemoryAPI
        from .knowledge.progress import ProgressService
        from .knowledge.sqlite_store import SQLiteStore

        store = SQLiteStore(db_path)
        progress = ProgressService(store).get_progress(course_id)
        memory = MemoryAPI(store)
        profile = memory.get_profile()
        evidence = memory.get_evidence_summary(course_id, concept_limit=10)
    except Exception as exc:
        return {
            "available": False,
            "reason": "knowledge_store_unavailable",
            "error_type": type(exc).__name__,
        }

    return {
        "available": True,
        "course_id": progress.course_id,
        "counts": {
            "total_concepts": progress.total_concepts,
            "mastered": progress.mastered,
            "stable": progress.stable,
            "decaying": progress.decaying,
            "critical": progress.critical,
        },
        "review_queue": progress.review_queue[:20],
        "recent_trusted_events": progress.recent_events,
        "weak_points": (profile.get("weak_points") or [])[:10],
        "learning_style": profile.get("learning_style") or "default",
        "evidence": evidence,
    }


def build_context(
    *,
    data_dir: Path | None = None,
    course_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical context document consumed by assistants.

    The function is intentionally read-only at the artifact level. It does
    not run aggregation, ingest documents, call an LLM, or mutate tasks.
    """
    config = default_config()
    root = Path(data_dir or config.data_dir).expanduser()
    if data_dir is not None:
        # A caller-provided data directory is an isolated runtime. Use its
        # conventional credential files unless the user explicitly overrides
        # them through environment variables.
        config.data_dir = root
        if not os.getenv("NEXUS_GOOGLE_CREDENTIALS"):
            config.google_credentials_path = root / "google_client_secret.json"
        if not os.getenv("NEXUS_GOOGLE_TOKEN"):
            config.google_token_path = root / "google_token.json"
        config.feeds_path = root / "feeds.json"
    generated_at = now or datetime.now(timezone.utc)
    tasks_path = root / "tasks.json"
    tasks = load_tasks(tasks_path)
    if course_id:
        tasks = [task for task in tasks if task.course == course_id]

    pipeline = _read_json(root / "pipeline_report.json")
    briefing = _read_json(root / "briefing.json")
    runtime = build_runtime_status(root)
    last_task_update = tasks_last_updated(tasks_path)
    pipeline_generated = pipeline.get("generated_at")
    source_status = build_sync_status(root, config=config, now=generated_at)

    return {
        "schema_version": "1.0",
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "scope": {"course_id": course_id, "data_dir": str(root)},
        "sync": {
            "last_task_update": _iso(last_task_update),
            "last_pipeline_run": pipeline_generated,
            "pipeline_ok": pipeline.get("ok") if isinstance(pipeline.get("ok"), bool) else None,
            "sources": source_status,
            "runtime": {
                "failure_queues": runtime.get("failure_queues", {}),
                "session_backlog": runtime.get("session_backlog", {}),
            },
        },
        "tasks": {
            "count": len(tasks),
            "items": [task.to_dict() for task in sorted(tasks, key=_task_sort_key)[:50]],
        },
        "briefing": {
            "available": bool(briefing),
            "generated_at": briefing.get("generated_at"),
            "todo": briefing.get("todo", [])[:10] if isinstance(briefing.get("todo"), list) else [],
            "schedule": briefing.get("schedule", [])[:10] if isinstance(briefing.get("schedule"), list) else [],
            "warnings": briefing.get("warnings", [])[:10] if isinstance(briefing.get("warnings"), list) else [],
        },
        "learning": _learning_context(root / "nexus.db", course_id),
    }
