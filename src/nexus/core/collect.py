"""Collect configured sources into tasks.json and record a step report."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .aggregation import AggregationResult, run_aggregation
from .config import AppConfig, default_config
from .io_utils import atomic_write_json
from .models import Task


@dataclass
class StepResult:
    name: str
    status: str
    message: str
    details: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def report_payload(steps: list[StepResult], generated_at: datetime) -> dict[str, Any]:
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


def runtime_config(config: AppConfig, data_dir: Path | None) -> AppConfig:
    """Point ``config`` at an explicit runtime directory and its credentials."""
    if data_dir is not None:
        root = Path(data_dir).expanduser()
        config.data_dir = root
        config.feeds_path = root / "feeds.json"
        if not os.getenv("NEXUS_GOOGLE_CREDENTIALS"):
            config.google_credentials_path = root / "google_client_secret.json"
        if not os.getenv("NEXUS_GOOGLE_TOKEN"):
            config.google_token_path = root / "google_token.json"
    return config


def aggregation_step(
    config: AppConfig,
    *,
    include_google: bool,
    email_enricher: Callable[[Task, str, str], Task] | None = None,
) -> StepResult:
    try:
        aggregation: AggregationResult = run_aggregation(
            include_google=include_google,
            data_dir=config.data_dir,
            config=config,
            email_enricher=email_enricher,
        )
    except Exception as exc:
        return StepResult(
            "aggregation", "failed", f"aggregation failed: {exc}",
            {"error_type": type(exc).__name__, "errors": [str(exc)]},
        )
    return StepResult(
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
    )


def write_report(
    steps: list[StepResult], generated_at: datetime, report_path: Path
) -> dict[str, Any]:
    report = report_payload(steps, generated_at)
    report["report_path"] = str(report_path.resolve())
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(report_path, report, ensure_ascii=False)
    except Exception as exc:
        steps.append(StepResult(
            "report_write", "failed", f"report write failed: {exc}",
            {"error_type": type(exc).__name__, "errors": [str(exc)]},
        ))
        report = report_payload(steps, generated_at)
        report["report_path"] = str(report_path.resolve())
    return report


def collect_sources(
    *,
    data_dir: Path | None = None,
    include_google: bool = False,
) -> dict[str, Any]:
    """Aggregate ICS/LMS/Calendar sources; always return a structured report."""
    config = runtime_config(default_config(), data_dir)
    now_local = datetime.now().astimezone()
    steps = [aggregation_step(config, include_google=include_google)]
    return write_report(steps, now_local, config.data_dir / "pipeline_report.json")
