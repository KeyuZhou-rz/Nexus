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
    assert launchd["ProgramArguments"][1:4] == ["-m", "nexus.cli.main", "sync"]
    assert "nexus.cli.pipeline" not in cron
    assert "nexus.cli.pipeline" not in service
    assert "nexus.cli.pipeline" not in launchd["ProgramArguments"]
    assert "NEXUS_DATA_DIR=/path/to/nexus-data" in cron
    assert "--data-dir $NEXUS_DATA_DIR" in cron
    assert "Environment=NEXUS_DATA_DIR=/var/lib/nexus" in service
    assert "--data-dir /var/lib/nexus" in service
    assert launchd["EnvironmentVariables"]["NEXUS_DATA_DIR"].endswith(
        "Library/Application Support/Nexus"
    )
    assert launchd["ProgramArguments"][-2:] == [
        "--data-dir",
        "/Users/you/Library/Application Support/Nexus",
    ]


def test_scheduler_documentation_requires_one_private_local_runtime() -> None:
    guidance = (ROOT / "deploy/README.md").read_text(encoding="utf-8")

    assert "same OS user" in guidance
    assert "NFS" in guidance
    assert "0700" in guidance
    assert "Desktop" in guidance
    assert "do not set `PYTHONPATH`" in guidance
    assert "`Umask` is `077`" in guidance
