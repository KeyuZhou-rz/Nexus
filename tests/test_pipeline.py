from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from nexus.aggregation import AggregationResult
from nexus.config import AppConfig
from nexus.pipeline import run_mvp_pipeline


@dataclass
class _BriefingStub:
    todo: list
    schedule: list
    warnings: list


def test_run_mvp_pipeline_writes_report_and_briefing(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("nexus.pipeline.default_config", lambda: AppConfig(data_dir=data_dir))
    monkeypatch.setattr("nexus.pipeline.run_aggregation", lambda include_google=True: AggregationResult(tasks=[], errors=[]))
    monkeypatch.setattr("nexus.pipeline.load_tasks", lambda: [])
    monkeypatch.setattr(
        "nexus.pipeline.build_briefing",
        lambda tasks, window_days, now, config, use_llm: _BriefingStub(todo=[], schedule=[], warnings=[]),
    )
    monkeypatch.setattr(
        "nexus.pipeline.briefing_payload",
        lambda briefing, window_start, window_end, generated_at=None: {
            "schema_version": "1.0",
            "todo": [],
            "schedule": [],
            "warnings": [],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "generated_at": generated_at.isoformat() if generated_at else None,
        },
    )

    report = run_mvp_pipeline(run_archive_sync=False, use_llm=False)

    assert report["ok"] is True
    assert [step["name"] for step in report["steps"]] == ["aggregation", "briefing"]

    briefing_path = data_dir / "briefing.json"
    report_path = data_dir / "pipeline_report.json"
    assert briefing_path.exists()
    assert report_path.exists()

    report_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert report_payload["ok"] is True


def test_run_mvp_pipeline_continues_when_archive_sync_fails(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("nexus.pipeline.default_config", lambda: AppConfig(data_dir=data_dir))
    monkeypatch.setattr(
        "nexus.pipeline.run_archive_sync_subprocess",
        lambda timeout=420: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("nexus.pipeline.run_aggregation", lambda include_google=True: AggregationResult(tasks=[], errors=[]))
    monkeypatch.setattr("nexus.pipeline.load_tasks", lambda: [])
    monkeypatch.setattr(
        "nexus.pipeline.build_briefing",
        lambda tasks, window_days, now, config, use_llm: _BriefingStub(todo=[], schedule=[], warnings=[]),
    )
    monkeypatch.setattr(
        "nexus.pipeline.briefing_payload",
        lambda briefing, window_start, window_end, generated_at=None: {
            "schema_version": "1.0",
            "todo": [],
            "schedule": [],
            "warnings": [],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "generated_at": generated_at.isoformat() if generated_at else None,
        },
    )

    report = run_mvp_pipeline(run_archive_sync=True, use_llm=False)

    assert report["ok"] is False
    names = [step["name"] for step in report["steps"]]
    assert names == ["archive_sync", "aggregation", "briefing"]
    assert report["steps"][0]["ok"] is False
