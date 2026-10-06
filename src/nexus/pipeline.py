from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .core.aggregation import AggregationResult, run_aggregation
from .archive_sync import run_archive_sync_subprocess
from .core.config import default_config
from .core.io_utils import atomic_write_json
from .intelligence.briefing import briefing_payload, build_briefing
from .core.storage import load_tasks


@dataclass
class StepResult:
    name: str
    status: str
    message: str
    details: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def _archive_configured() -> bool:
    base_url = os.getenv("NEXUS_BRIGHTSPACE_URL", "").strip()
    if not base_url:
        return False
    mode = os.getenv("NEXUS_BRIGHTSPACE_LOGIN_MODE", "manual").strip().lower()
    if mode in {"auto", "hybrid"}:
        return bool(
            os.getenv("NEXUS_BRIGHTSPACE_USERNAME", "").strip()
            and os.getenv("NEXUS_BRIGHTSPACE_PASSWORD", "").strip()
        )
    return True


def _report_payload(steps: list[StepResult], generated_at: datetime) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "generated_at": generated_at.isoformat(),
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "ok": step.ok,
                "message": step.message,
                "details": step.details,
            }
            for step in steps
        ],
        "ok": all(step.ok for step in steps),
        "warnings": [
            warning
            for step in steps
            for warning in step.details.get("warnings", [])
        ],
    }


def run_mvp_pipeline(
    *,
    data_dir: Path | None = None,
    run_archive_sync: bool = True,
    archive_timeout: int = 420,
    include_google: bool = False,
    use_llm: bool = False,
    window_days: int = 7,
    briefing_output: Path | None = None,
    report_output: Path | None = None,
) -> dict[str, Any]:
    """Run the pipeline and always return a structured status report."""
    config = default_config()
    if data_dir is not None:
        root = Path(data_dir).expanduser()
        config.data_dir = root
        config.feeds_path = root / "feeds.json"
        if not os.getenv("NEXUS_GOOGLE_CREDENTIALS"):
            config.google_credentials_path = root / "google_client_secret.json"
        if not os.getenv("NEXUS_GOOGLE_TOKEN"):
            config.google_token_path = root / "google_token.json"
    now_local = datetime.now().astimezone()
    briefing_path = briefing_output or (config.data_dir / "briefing.json")
    report_path = report_output or (config.data_dir / "pipeline_report.json")
    steps: list[StepResult] = []

    if not run_archive_sync:
        steps.append(StepResult(
            "archive_sync", "skipped", "Archive sync disabled by command option.",
            {"warnings": ["Archive sync was not requested."]},
        ))
    elif not _archive_configured():
        steps.append(StepResult(
            "archive_sync", "skipped", "Archive sync is not configured.",
            {"warnings": ["NEXUS_BRIGHTSPACE_URL or login credentials are not configured."]},
        ))
    else:
        try:
            archive_result = run_archive_sync_subprocess(
                timeout=archive_timeout,
                data_dir=config.data_dir,
            )
            archive_ok = str(archive_result.get("status", "")) == "success"
            steps.append(StepResult(
                "archive_sync",
                "succeeded" if archive_ok else "failed",
                str(archive_result.get("message", "archive_sync finished")),
                {
                    "source_status": archive_result.get("status"),
                    "tasks": len(archive_result.get("tasks", []) or []),
                    "archives": len(archive_result.get("archives", []) or []),
                    "archive_failures": len(archive_result.get("archive_failures", []) or []),
                    "post_ingest": archive_result.get("post_ingest", {}),
                },
            ))
        except Exception as exc:
            steps.append(StepResult(
                "archive_sync", "failed", f"archive_sync failed: {exc}",
                {"error_type": type(exc).__name__},
            ))

    try:
        aggregation: AggregationResult = run_aggregation(
            include_google=include_google,
            data_dir=config.data_dir,
            config=config,
        )
        steps.append(StepResult(
            "aggregation",
            "failed" if aggregation.errors else "succeeded",
            "; ".join(aggregation.errors) if aggregation.errors else "aggregation completed",
            {
                "task_count": len(aggregation.tasks),
                "errors": aggregation.errors,
                "warnings": aggregation.warnings,
                "sources": aggregation.source_status,
                "failure_queue": aggregation.failure_queue,
            },
        ))
    except Exception as exc:
        steps.append(StepResult(
            "aggregation", "failed", f"aggregation failed: {exc}",
            {"error_type": type(exc).__name__, "errors": [str(exc)]},
        ))

    try:
        tasks = load_tasks(config.data_dir / "tasks.json")
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
        steps.append(StepResult(
            "briefing", "succeeded", "briefing generated",
            {
                "output": str(briefing_path.resolve()),
                "todo": len(payload.get("todo", [])),
                "schedule": len(payload.get("schedule", [])),
                "warnings": payload.get("warnings", []),
            },
        ))
    except Exception as exc:
        steps.append(StepResult(
            "briefing", "failed", f"briefing failed: {exc}",
            {"error_type": type(exc).__name__, "errors": [str(exc)]},
        ))

    report = _report_payload(steps, now_local)
    report["report_path"] = str(report_path.resolve())
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(report_path, report, ensure_ascii=False)
    except Exception as exc:
        steps.append(StepResult(
            "report_write", "failed", f"report write failed: {exc}",
            {"error_type": type(exc).__name__, "errors": [str(exc)]},
        ))
        report = _report_payload(steps, now_local)
        report["report_path"] = str(report_path.resolve())
    return report
