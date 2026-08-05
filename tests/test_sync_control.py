from __future__ import annotations

from pathlib import Path

from nexus.config import AppConfig
from nexus.io_utils import atomic_write_json
from nexus.sync_control import build_sync_status


def test_sync_control_reports_configured_fresh_and_task_counts(tmp_path: Path) -> None:
    feeds = [{
        "kind": "brightspace_ical",
        "name": "OS",
        "url": "https://brightspace.example/feed.ics",
        "course": "CS202_OS",
        "enabled": True,
    }]
    atomic_write_json(tmp_path / "feeds.json", feeds)
    atomic_write_json(tmp_path / "tasks.json", [{
        "schema_version": "1.0",
        "id": "brightspace:1",
        "title": "Lab",
        "due_at": "2026-08-06T09:00:00+00:00",
        "source": "brightspace",
        "course": "CS202_OS",
        "status": "open",
        "priority": 1,
        "tags": ["assignment"],
    }])
    atomic_write_json(tmp_path / "pipeline_report.json", {
        "ok": True,
        "generated_at": "2026-08-05T08:00:00+00:00",
        "sources": {"brightspace": "succeeded"},
    })

    status = build_sync_status(
        tmp_path,
        config=AppConfig(data_dir=tmp_path),
        now=__import__("datetime").datetime.fromisoformat("2026-08-05T09:00:00+00:00"),
    )

    assert status["brightspace"] == {
        "source": "brightspace",
        "enabled": True,
        "configured": True,
        "status": "succeeded",
        "freshness": "fresh",
        "last_success_at": "2026-08-05T08:00:00+00:00",
        "item_count": 1,
        "error": None,
    }
    assert status["google"]["status"] == "disabled"


def test_sync_control_marks_failed_sources_stale_with_queue_error(tmp_path: Path) -> None:
    atomic_write_json(tmp_path / "feeds.json", [{
        "kind": "brightspace_rss",
        "name": "OS",
        "url": "https://brightspace.example/feed.xml",
        "enabled": True,
    }])
    atomic_write_json(tmp_path / "pipeline_report.json", {
        "ok": False,
        "generated_at": "2026-08-04T08:00:00+00:00",
        "sources": {"brightspace": "failed"},
    })
    atomic_write_json(tmp_path / "aggregation_failures.json", {
        "generated_at": "2026-08-05T08:00:00+00:00",
        "failure_count": 1,
        "failures": [{
            "operation": "aggregation",
            "source": "brightspace",
            "error": "timeout",
        }],
    })

    status = build_sync_status(
        tmp_path,
        config=AppConfig(data_dir=tmp_path),
        now=__import__("datetime").datetime.fromisoformat("2026-08-05T09:00:00+00:00"),
    )

    assert status["brightspace"]["status"] == "failed"
    assert status["brightspace"]["freshness"] == "stale"
    assert status["brightspace"]["error"] == "timeout"


def test_sync_control_reads_current_pipeline_report_and_google_aliases(tmp_path: Path) -> None:
    atomic_write_json(tmp_path / "tasks.json", [
        {"id": "gcal:1", "title": "Calendar", "source": "gcal", "status": "open"},
        {"id": "gmail:1", "title": "Mail", "source": "gmail", "status": "open"},
        {"id": "brightspace:1", "title": "LMS", "source": "brightspace", "status": "open"},
    ])
    atomic_write_json(tmp_path / "pipeline_report.json", {
        "generated_at": "2026-08-05T08:00:00+00:00",
        "steps": [{
            "name": "aggregation",
            "details": {"sources": {
                "brightspace": "skipped",
                "google": "succeeded",
                "canvas": "disabled",
            }},
        }],
    })

    status = build_sync_status(
        tmp_path,
        config=AppConfig(data_dir=tmp_path),
        now=__import__("datetime").datetime.fromisoformat("2026-08-05T09:00:00+00:00"),
    )

    assert status["google"]["status"] == "succeeded"
    assert status["google"]["item_count"] == 2
    assert status["brightspace"]["status"] == "disabled"
