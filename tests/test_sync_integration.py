from __future__ import annotations

import json
from pathlib import Path
import stat

from nexus.core.sync_service import run_sync


def test_run_sync_offline_persists_all_artifacts_in_requested_data_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "isolated-runtime"
    ambient_data_dir = tmp_path / "unused-ambient-runtime"
    monkeypatch.setenv("NEXUS_DATA_DIR", str(ambient_data_dir))
    for name in (
        "NEXUS_GOOGLE_CREDENTIALS",
        "NEXUS_GOOGLE_TOKEN",
        "NEXUS_BRIGHTSPACE_URL",
        "NEXUS_BRIGHTSPACE_USERNAME",
        "NEXUS_BRIGHTSPACE_PASSWORD",
        "NEXUS_CANVAS_URL",
        "NEXUS_CANVAS_TOKEN",
        "NEXUS_LLM_ENABLED",
        "NEXUS_LLM_BASE_URL",
        "NEXUS_LLM_API_KEY",
        "NEXUS_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    def unexpected_external_call(*args, **kwargs):
        raise AssertionError("offline sync attempted to call an external provider")

    monkeypatch.setattr(
        "nexus.core.aggregation.BrightspaceAggregator.fetch_tasks",
        unexpected_external_call,
    )
    monkeypatch.setattr(
        "nexus.core.aggregation.GoogleAggregator.fetch_tasks",
        unexpected_external_call,
    )
    monkeypatch.setattr(
        "nexus.core.aggregation.CanvasLMSConnector.to_tasks",
        unexpected_external_call,
    )

    receipt = run_sync(
        data_dir=data_dir,
        trigger="integration-test",
        include_google=False,
    )

    artifact_names = (
        "tasks.json",
        "pipeline_report.json",
        "sync_receipt.json",
    )
    artifact_paths = [data_dir / name for name in artifact_names]
    assert [path.is_file() for path in artifact_paths] == [True, True, True]
    assert not (data_dir / "briefing.json").exists()
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert {path.resolve().parent for path in artifact_paths} == {data_dir.resolve()}
    assert not ambient_data_dir.exists()
    assert not (data_dir / ".sync.lock").exists()

    tasks = json.loads((data_dir / "tasks.json").read_text(encoding="utf-8"))
    report = json.loads((data_dir / "pipeline_report.json").read_text(encoding="utf-8"))
    saved_receipt = json.loads(
        (data_dir / "sync_receipt.json").read_text(encoding="utf-8")
    )

    assert tasks == []
    assert report["ok"] is True
    assert [(step["name"], step["status"]) for step in report["steps"]] == [
        ("aggregation", "succeeded"),
    ]
    assert Path(report["report_path"]).resolve() == (
        data_dir / "pipeline_report.json"
    ).resolve()
    assert saved_receipt == receipt
    assert receipt["status"] == "succeeded"
    assert receipt["trigger"] == "integration-test"
    assert Path(receipt["data_dir"]).resolve() == data_dir.resolve()
    assert Path(receipt["pipeline_report"]).resolve() == (
        data_dir / "pipeline_report.json"
    ).resolve()
