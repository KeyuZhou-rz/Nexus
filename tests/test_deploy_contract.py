from __future__ import annotations

from pathlib import Path
import plistlib


ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_examples_use_single_flight_sync_entrypoint() -> None:
    cron = (ROOT / "deploy/nexus-pipeline.cron").read_text(encoding="utf-8")
    service = (ROOT / "deploy/nexus-pipeline.service").read_text(encoding="utf-8")
    with (ROOT / "deploy/com.nexus.pipeline.plist").open("rb") as handle:
        launchd = plistlib.load(handle)

    assert "python -m nexus.cli.main sync" in cron
    assert "ExecStart=python -m nexus.cli.main sync" in service
    assert launchd["ProgramArguments"][-3:] == ["-m", "nexus.cli.main", "sync"]
    assert "nexus.cli.pipeline" not in cron
    assert "nexus.cli.pipeline" not in service
    assert "nexus.cli.pipeline" not in launchd["ProgramArguments"]
