from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from nexus.knowledge.sqlite_store import SQLiteStore


PROJECT_ROOT = Path(__file__).parents[1]


def _run_cli(*args: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
        "NEXUS_DATA_DIR": str(data_dir),
        "NEXUS_LLM_ENABLED": "0",
        "NEXUS_BRIGHTSPACE_URL": "",
        "NEXUS_BRIGHTSPACE_USERNAME": "",
        "NEXUS_BRIGHTSPACE_PASSWORD": "",
        "NEXUS_CANVAS_URL": "",
        "NEXUS_CANVAS_TOKEN": "",
    }
    return subprocess.run(
        [sys.executable, "-m", "nexus.cli.main", *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_clean_room_offline_pipeline_and_runtime_commands(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"

    pipeline = _run_cli(
        "pipeline", "--skip-archive-sync", "--no-google", data_dir=data_dir
    )
    assert pipeline.returncode == 0, pipeline.stderr
    pipeline_payload = json.loads(pipeline.stdout)
    assert pipeline_payload["ok"] is True
    assert (data_dir / "briefing.json").exists()
    assert (data_dir / "pipeline_report.json").exists()

    doctor = _run_cli("doctor", "--no-google", data_dir=data_dir)
    assert doctor.returncode == 0, doctor.stderr
    doctor_payload = json.loads(doctor.stdout)
    assert doctor_payload["ok"] is True
    assert doctor_payload["details"]["data_dir"] == str(data_dir)
    assert any("Chroma index not initialized" in item for item in doctor_payload["warnings"])

    status = _run_cli("status", "--data-dir", str(data_dir), data_dir=data_dir)
    assert status.returncode == 0, status.stderr
    status_payload = json.loads(status.stdout)
    assert status_payload["data_dir"] == str(data_dir)

    SQLiteStore(data_dir / "nexus.db")
    (data_dir / "chroma").mkdir(parents=True)
    audit = _run_cli(
        "db-check",
        "--sqlite-path",
        str(data_dir / "nexus.db"),
        "--db-dir",
        str(data_dir / "chroma"),
        "--json",
        data_dir=data_dir,
    )
    assert audit.returncode == 0, audit.stderr
    assert json.loads(audit.stdout)["ok"] is True


def test_synthetic_demo_closes_sync_memory_and_context_loop(tmp_path: Path) -> None:
    data_dir = tmp_path / "demo"

    demo = _run_cli("demo", "--data-dir", str(data_dir), "--json", data_dir=data_dir)
    assert demo.returncode == 0, demo.stderr
    demo_payload = json.loads(demo.stdout)
    assert demo_payload["synthetic"] is True
    assert demo_payload["task_count"] == 2
    assert demo_payload["evidence_count"] == 1

    sync = _run_cli("sync-status", "--data-dir", str(data_dir), "--json", data_dir=data_dir)
    assert sync.returncode == 0, sync.stderr
    sync_payload = json.loads(sync.stdout)
    assert sync_payload["sources"]["brightspace"]["configured"] is True
    assert sync_payload["sources"]["brightspace"]["status"] == "succeeded"

    context = _run_cli(
        "context", "--data-dir", str(data_dir), "--course-id", "CS202_OS", "--json",
        data_dir=data_dir,
    )
    assert context.returncode == 0, context.stderr
    context_payload = json.loads(context.stdout)
    assert context_payload["tasks"]["count"] == 2
    assert context_payload["briefing"]["available"] is True
    assert context_payload["learning"]["available"] is True
    assert context_payload["learning"]["evidence"][0]["source"] == "demo_assessment"

    calibration = _run_cli(
        "calibration",
        "--fixture",
        str(PROJECT_ROOT / "tests" / "fixtures" / "mastery_calibration.json"),
        "--json",
        data_dir=data_dir,
    )
    assert calibration.returncode == 0, calibration.stderr
    assert json.loads(calibration.stdout)["ok"] is True
