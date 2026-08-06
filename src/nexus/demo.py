"""Synthetic, isolated demo runtime for product evaluation and release smoke tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .context_api import build_context
from .io_utils import atomic_write_json, atomic_write_text
from .knowledge.memory_api import MemoryAPI
from .knowledge.sqlite_store import SQLiteStore
from .models import FeedSource, Task
from .onboarding import initialize_runtime, upsert_feed
from .storage import save_tasks


DEMO_MARKER = ".nexus-demo"


def create_demo_runtime(data_dir: Path, *, now: datetime | None = None) -> dict[str, object]:
    """Create a complete synthetic snapshot in an empty directory."""
    root = Path(data_dir).expanduser()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Demo data directory must be empty")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    initialize_runtime(root)
    atomic_write_text(root / DEMO_MARKER, "synthetic demo data\n")

    ical_path = root / "demo_calendar.ics"
    due = current + timedelta(days=1, hours=2)
    atomic_write_text(
        ical_path,
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Nexus//Demo//EN\n"
        "BEGIN:VEVENT\nUID:nexus-demo-lab\n"
        f"DTSTART:{due.strftime('%Y%m%dT%H%M%SZ')}\n"
        "SUMMARY:Operating Systems Lab\nEND:VEVENT\nEND:VCALENDAR\n",
    )
    upsert_feed(
        root,
        FeedSource(
            kind="ical_file",
            name="Operating Systems Demo",
            url=ical_path.resolve().as_uri(),
            course="CS202_OS",
        ),
    )

    tasks = [
        Task(
            id="demo:lab",
            title="Operating Systems Lab",
            due_at=due,
            source="brightspace",
            course="CS202_OS",
            priority=3,
            tags=["assignment", "deadline", "demo"],
        ),
        Task(
            id="demo:office-hours",
            title="Concurrency Office Hours",
            due_at=current + timedelta(days=3),
            source="gcal",
            course="CS202_OS",
            priority=1,
            tags=["meeting", "demo"],
        ),
    ]
    save_tasks(tasks, root / "tasks.json")

    generated_at = current.isoformat()
    atomic_write_json(root / "briefing.json", {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "todo": [{
            "text_en": "Finish the operating systems lab before tomorrow's deadline.",
            "text_zh": "在明天截止前完成操作系统实验。",
            "source_ids": ["demo:lab"],
        }],
        "schedule": [{
            "text_en": "Review deadlock before concurrency office hours.",
            "text_zh": "在并发答疑前复习死锁。",
            "source_ids": ["demo:office-hours"],
        }],
        "warnings": [],
    })
    atomic_write_json(root / "pipeline_report.json", {
        "schema_version": "1.1",
        "generated_at": generated_at,
        "ok": True,
        "steps": [
            {
                "name": "archive_sync",
                "status": "skipped",
                "ok": True,
                "message": "Synthetic demo runtime.",
                "details": {},
            },
            {
                "name": "aggregation",
                "status": "succeeded",
                "ok": True,
                "message": "Synthetic tasks generated.",
                "details": {
                    "task_count": len(tasks),
                    "sources": {
                        "brightspace": "succeeded",
                        "google": "disabled",
                        "canvas": "disabled",
                    },
                },
            },
            {
                "name": "briefing",
                "status": "succeeded",
                "ok": True,
                "message": "Synthetic briefing generated.",
                "details": {"todo": 1, "schedule": 1},
            },
        ],
        "warnings": [],
    })

    store = SQLiteStore(root / "nexus.db")
    store.upsert_concept("cs202_os__deadlock", "Deadlock", "CS202_OS")
    MemoryAPI(store).record_event(
        "cs202_os__deadlock",
        "struggled",
        session_id="demo-assessment",
        context={
            "evidence_type": "assessment",
            "source": "demo_assessment",
            "confidence": 0.88,
        },
    )

    context = build_context(data_dir=root, course_id="CS202_OS", now=current)
    return {
        "schema_version": "1.0",
        "ok": True,
        "synthetic": True,
        "data_dir": str(root),
        "task_count": context["tasks"]["count"],
        "source_count": len(context["sync"]["sources"]),
        "learning_available": context["learning"]["available"],
        "evidence_count": len(context["learning"].get("evidence") or []),
    }
