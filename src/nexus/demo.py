"""Synthetic, isolated demo runtime for product evaluation and release smoke tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core.aggregation import run_aggregation
from .core.config import AppConfig
from .core.context_api import build_context, persist_next_action
from .core.io_utils import atomic_write_json, atomic_write_text
from .knowledge.memory_api import MemoryAPI
from .knowledge.sqlite_store import SQLiteStore
from .learning_focus import knowledge_learning
from .core.models import FeedSource, Task
from .core.onboarding import initialize_runtime, upsert_feed
from .core.storage import save_tasks


DEMO_MARKER = ".nexus-demo"
_SEEDABLE_NAMES = {
    "feeds.json",
    "tasks.json",
    "nexus.db",
    "nexus.db-wal",
    "nexus.db-shm",
    ".DS_Store",
}


def runtime_is_seedable(data_dir: Path) -> bool:
    """True when a practice week can be generated without wiping user data."""
    root = Path(data_dir).expanduser()
    if not root.exists():
        return True
    if (root / DEMO_MARKER).is_file():
        return False
    leftover = [path.name for path in root.iterdir() if path.name not in _SEEDABLE_NAMES]
    if leftover:
        return False
    for name in ("feeds.json", "tasks.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if payload not in ([], {}):
            return False
    return True


def create_demo_runtime(
    data_dir: Path,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Create a complete synthetic snapshot in an empty or freshly initialized directory."""
    import shutil

    root = Path(data_dir).expanduser()
    if force and root.exists() and (root / DEMO_MARKER).is_file():
        shutil.rmtree(root)
    if root.exists() and not runtime_is_seedable(root):
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

    config = AppConfig(data_dir=root, feeds_path=root / "feeds.json")
    aggregation = run_aggregation(
        include_google=False,
        include_canvas=False,
        data_dir=root,
        config=config,
    )
    if aggregation.errors or len(aggregation.tasks) != 1:
        raise RuntimeError("Synthetic iCal acquisition did not produce exactly one task")

    tasks = [
        Task(
            id="demo:homework-1",
            title="Operating Systems Homework 1",
            due_at=current - timedelta(hours=18),
            source="manual",
            course="CS202_OS",
            priority=2,
            tags=["manual", "assignment", "demo"],
        ),
        *aggregation.tasks,
        Task(
            id="demo:physics-exam",
            title="Physics exam",
            due_at=current + timedelta(days=3),
            source="manual",
            course="Physics",
            priority=3,
            tags=["manual", "exam", "demo"],
        ),
        Task(
            id="demo:graphics-project",
            title="Graphics project",
            due_at=current + timedelta(days=4),
            source="manual",
            course="Graphics",
            priority=3,
            tags=["manual", "project", "demo"],
        ),
        Task(
            id="demo:assignment-4",
            title="Assignment 4",
            due_at=current + timedelta(days=2),
            source="manual",
            course="CS",
            priority=2,
            tags=["manual", "assignment", "demo"],
        ),
        Task(
            id="demo:quiz-1",
            title="Concurrency Quiz",
            due_at=current + timedelta(days=5),
            source="manual",
            course="CS202_OS",
            priority=2,
            tags=["manual", "quiz", "demo"],
        ),
        Task(
            id="demo:exam",
            title="Operating Systems Midterm",
            due_at=current + timedelta(days=12),
            source="manual",
            course="CS202_OS",
            priority=3,
            tags=["manual", "exam", "midterm", "demo"],
        ),
        Task(
            id="demo:holiday",
            title="Chinese Valentine's Day",
            due_at=current + timedelta(days=2),
            source="gcal",
            course="Holidays in China",
            priority=0,
            tags=["calendar"],
        ),
        Task(
            id="demo:office-hours",
            title="Concurrency Office Hours",
            due_at=current + timedelta(days=3),
            source="manual",
            course="CS202_OS",
            priority=1,
            tags=["meeting", "demo"],
        ),
    ]
    save_tasks(tasks, root / "tasks.json")
    from .core.academic_state import persist_obligations

    persist_obligations(root, tasks)
    for task in tasks:
        if task.id == "demo:assignment-4":
            task.due_at = current + timedelta(days=5)
            break
    persist_obligations(root, tasks)
    atomic_write_json(root / "completions.json", {
        "demo:prior-lab": {
            "done_at": (current - timedelta(days=6)).isoformat(),
            "started_at": (current - timedelta(days=6, hours=3)).isoformat(),
            "effort_minutes": 180,
            "effort_key": "lab",
        },
        "demo:prior-hw": {
            "done_at": (current - timedelta(days=4)).isoformat(),
            "started_at": (current - timedelta(days=4, hours=2)).isoformat(),
            "effort_minutes": 90,
            "effort_key": "assignment",
        },
        "demo:graphics-hw-a": {
            "done_at": (current - timedelta(days=10, hours=3)).isoformat(),
            "due_at": (current - timedelta(days=10)).isoformat(),
            "effort_minutes": 120,
            "effort_key": "assignment",
            "course": "Graphics",
        },
        "demo:graphics-hw-b": {
            "done_at": (current - timedelta(days=6, hours=3)).isoformat(),
            "due_at": (current - timedelta(days=6)).isoformat(),
            "effort_minutes": 110,
            "effort_key": "assignment",
            "course": "Graphics",
        },
        "demo:graphics-old-project": {
            "done_at": (current - timedelta(days=8)).isoformat(),
            "due_at": (current - timedelta(days=7, hours=20)).isoformat(),
            "effort_minutes": 450,
            "effort_key": "project",
            "course": "Graphics",
        },
    })

    generated_at = current.isoformat()
    atomic_write_json(root / "briefing.json", {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "todo": [{
            "text_en": "Finish Operating Systems Homework 1 — it is already overdue.",
            "text_zh": "先补操作系统作业 1，已经过期。",
            "source_ids": ["demo:homework-1"],
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
                        **aggregation.source_status,
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

    context = build_context(
        data_dir=root, course_id="CS202_OS", now=current, learning=knowledge_learning
    )
    persist_next_action(data_dir=root, now=current)
    from .core.academic_state_service import AcademicStateService

    AcademicStateService(root).import_legacy_completions(now=current)
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


def run_v1_loop_scenario(
    data_dir: Path,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, object]:
    """Account-free V1 loop proof: state, deadline delta, conflict, action, rerank."""
    from datetime import timedelta

    from .core.academic_state_service import AcademicStateService
    from .core.action_service import ActionService
    from .core.today import build_today
    from .core.v1_config import V1_NOW, V1_TZ

    root = Path(data_dir).expanduser()
    if force and root.exists() and (root / DEMO_MARKER).is_file():
        import shutil

        shutil.rmtree(root)
    if root.exists() and not runtime_is_seedable(root):
        raise ValueError("Demo data directory must be empty")

    initialize_runtime(root)
    atomic_write_text(root / DEMO_MARKER, "synthetic v1 loop\n")
    current = now or V1_NOW
    if current.tzinfo is None:
        current = current.replace(tzinfo=V1_TZ)

    state = AcademicStateService(root)
    cs = state.add_manual_obligation(
        title="CS Assignment 2",
        course="CS101",
        due_at=datetime(2026, 9, 14, 9, 0, tzinfo=V1_TZ),
        estimated_hours=6,
        remaining_hours=6,
        kind="assignment",
        now=current,
    )
    history = state.add_manual_obligation(
        title="History Reading",
        course="HIST101",
        due_at=datetime(2026, 9, 9, 9, 0, tzinfo=V1_TZ),
        estimated_hours=1,
        remaining_hours=1,
        kind="reading",
        now=current,
    )
    for weekday, start, end in (
        (0, "14:00", "18:00"),
        (1, "16:00", "18:00"),
        (2, "16:00", "18:00"),
        (3, "16:00", "18:00"),
        (4, "16:00", "18:00"),
    ):
        state.add_availability_rule(
            weekday=weekday,
            starts_local=start,
            ends_local=end,
            timezone="America/New_York",
        )
    cs_id = str(cs["id"])
    history_id = str(history["id"])
    state.update_obligation(
        cs_id,
        due_at=datetime(2026, 9, 8, 9, 0, tzinfo=V1_TZ),
        now=current,
    )
    state.upsert_calendar_block(
        source="gcal",
        external_id="conflict-mon-16-18",
        title="Occupied block",
        starts_at=datetime(2026, 9, 7, 16, 0, tzinfo=V1_TZ),
        ends_at=datetime(2026, 9, 7, 18, 0, tzinfo=V1_TZ),
        now=current,
    )
    today = build_today(root, now=current)
    rec = today.recommendation
    if rec is None or rec.obligation_id != cs_id:
        raise RuntimeError("V1 loop did not recommend CS Assignment 2")
    cs_risk = next(item for item in today.assessments if item.obligation_id == cs_id)
    if cs_risk.band != "high" or "remaining_exceeds_available" not in cs_risk.reasons:
        raise RuntimeError("V1 loop did not mark remaining-vs-available as high risk")

    actions = ActionService(root)
    actions.accept(
        cs_id,
        block_minutes=rec.block_minutes,
        recommendation_key=rec.recommendation_key,
        now=current,
    )
    actions.start(cs_id, recommendation_key=rec.recommendation_key, now=current)
    done_at = current + timedelta(minutes=90)
    actions.complete(cs_id, now=done_at)
    after = build_today(root, now=done_at)
    if after.recommendation is None or after.recommendation.obligation_id != history_id:
        raise RuntimeError("V1 loop did not rerank to History Reading after completion")

    snapshot = state.load_state()
    due_changed = sum(
        1 for item in snapshot["state_changes"] if item.get("kind") == "due_changed"
    )
    return {
        "schema_version": "1.0",
        "ok": True,
        "synthetic": True,
        "scenario": "v1-loop",
        "network": False,
        "data_dir": str(root),
        "obligation_count": len(snapshot["obligations"]),
        "due_changed": due_changed,
        "risk_band": cs_risk.band,
        "reasons": list(cs_risk.reasons),
        "remaining_hours": cs_risk.remaining_hours,
        "available_hours": cs_risk.available_hours,
        "top_obligation_id": cs_id,
        "recommendation": {
            "obligation_id": rec.obligation_id,
            "action": rec.action,
            "block_minutes": rec.block_minutes,
            "risk_band": rec.risk_band,
            "recommendation_key": rec.recommendation_key,
        },
        "action_event_count": len(state.store.list_action_events(limit=200)),
        "effort_sample_count": len(state.store.list_effort_samples()),
        "next_obligation_id": after.recommendation.obligation_id,
        "completed_status": state.store.get_obligation(cs_id)["status"],
    }
