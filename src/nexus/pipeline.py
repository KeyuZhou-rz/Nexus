from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .aggregation import run_aggregation
from .archive_sync import run_archive_sync_subprocess
from .config import default_config
from .io_utils import atomic_write_json
from .intelligence.briefing import briefing_payload, build_briefing
from .storage import load_tasks


@dataclass
class StepResult:
    name: str
    ok: bool
    message: str
    details: dict[str, Any]



def run_mvp_pipeline(
    *,
    run_archive_sync: bool = True,
    archive_timeout: int = 420,
    include_google: bool = True,
    use_llm: bool = True,
    window_days: int = 7,
    briefing_output: Path | None = None,
    report_output: Path | None = None,
) -> dict[str, Any]:
    """Run the integrated MVP pipeline and return a structured report."""
    config = default_config()
    now_local = datetime.now().astimezone()

    briefing_path = briefing_output or (config.data_dir / "briefing.json")
    report_path = report_output or (config.data_dir / "pipeline_report.json")

    steps: list[StepResult] = []

    if run_archive_sync:
        try:
            archive_result = run_archive_sync_subprocess(timeout=archive_timeout)
            archive_ok = str(archive_result.get("status", "")) == "success"
            steps.append(
                StepResult(
                    name="archive_sync",
                    ok=archive_ok,
                    message=str(archive_result.get("message", "archive_sync finished")),
                    details={
                        "status": archive_result.get("status"),
                        "tasks": len(archive_result.get("tasks", []) or []),
                        "archives": len(archive_result.get("archives", []) or []),
                        "archive_failures": len(archive_result.get("archive_failures", []) or []),
                        "post_ingest": archive_result.get("post_ingest", {}),
                    },
                )
            )
        except Exception as exc:
            steps.append(
                StepResult(
                    name="archive_sync",
                    ok=False,
                    message=f"archive_sync failed: {exc}",
                    details={},
                )
            )

    agg = run_aggregation(include_google=include_google)
    steps.append(
        StepResult(
            name="aggregation",
            ok=not agg.errors,
            message="aggregation completed" if not agg.errors else "; ".join(agg.errors),
            details={"task_count": len(agg.tasks), "errors": agg.errors},
        )
    )

    tasks = load_tasks()
    window_start = now_local
    window_end = now_local + timedelta(days=window_days)
    briefing = build_briefing(
        tasks,
        window_days=window_days,
        now=now_local,
        config=config,
        use_llm=use_llm,
    )
    payload = briefing_payload(briefing, window_start, window_end, generated_at=now_local)
    briefing_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(briefing_path, payload, ensure_ascii=False)
    steps.append(
        StepResult(
            name="briefing",
            ok=True,
            message="briefing generated",
            details={
                "output": str(briefing_path.resolve()),
                "todo": len(payload.get("todo", [])),
                "schedule": len(payload.get("schedule", [])),
                "warnings": payload.get("warnings", []),
            },
        )
    )

    report = {
        "schema_version": "1.0",
        "generated_at": now_local.isoformat(),
        "steps": [
            {
                "name": s.name,
                "ok": s.ok,
                "message": s.message,
                "details": s.details,
            }
            for s in steps
        ],
        "ok": all(s.ok for s in steps),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_path, report, ensure_ascii=False)
    report["report_path"] = str(report_path.resolve())
    return report
