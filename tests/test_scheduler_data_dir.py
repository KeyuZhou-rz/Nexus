from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_db_backup_defaults_follow_configured_data_dir(tmp_path, monkeypatch, capsys) -> None:
    from nexus.cli import db_backup

    data_dir = tmp_path / "private-runtime"
    unrelated_cwd = tmp_path / "working-directory"
    unrelated_cwd.mkdir()
    captured: dict[str, Path] = {}

    def fake_create_backup(sqlite_path, chroma_path, output_path, **_kwargs):
        captured.update(
            sqlite_path=sqlite_path,
            chroma_path=chroma_path,
            output_path=output_path,
        )
        return {"ok": True, "archive": str(output_path)}

    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.chdir(unrelated_cwd)
    monkeypatch.setattr("nexus.knowledge.maintenance.create_backup", fake_create_backup)
    monkeypatch.setattr(sys, "argv", ["db-backup"])

    assert db_backup.main() == 0
    assert captured["sqlite_path"] == data_dir / "nexus.db"
    assert captured["chroma_path"] == data_dir / "chroma"
    assert captured["output_path"].parent == data_dir / "backups"
    assert captured["output_path"].name.startswith("nexus-")
    assert "{timestamp}" not in captured["output_path"].name
    assert not (unrelated_cwd / "data").exists()
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert json.loads((data_dir / "backup_report.json").read_text())["ok"] is True


def test_cron_jobs_share_one_explicit_absolute_runtime() -> None:
    sync = (ROOT / "deploy/nexus-pipeline.cron").read_text(encoding="utf-8")
    backup = (ROOT / "deploy/nexus-backup.cron").read_text(encoding="utf-8")
    sessions = (ROOT / "deploy/nexus-session-finalize.cron").read_text(encoding="utf-8")

    for template in (sync, backup, sessions):
        assert "NEXUS_DATA_DIR=/path/to/nexus-data" in template
        assert ">> $NEXUS_DATA_DIR/" in template
        assert "/path/to/Nexus/data" not in template
    assert "--sqlite-path $NEXUS_DATA_DIR/nexus.db" in backup
    assert "--db-dir $NEXUS_DATA_DIR/chroma" in backup
    assert "--output $NEXUS_DATA_DIR/backups/" in backup
    assert "nexus-{timestamp}.zip" in backup
    assert "--db-path $NEXUS_DATA_DIR/nexus.db" in sessions


def test_systemd_backup_matches_sync_runtime_and_private_log_dir() -> None:
    sync = (ROOT / "deploy/nexus-pipeline.service").read_text(encoding="utf-8")
    backup = (ROOT / "deploy/nexus-backup.service").read_text(encoding="utf-8")

    assert "Environment=NEXUS_DATA_DIR=/var/lib/nexus" in sync
    assert "Environment=NEXUS_DATA_DIR=/var/lib/nexus" in backup
    assert "--sqlite-path /var/lib/nexus/nexus.db" in backup
    assert "--db-dir /var/lib/nexus/chroma" in backup
    assert "--output /var/lib/nexus/backups/" in backup
    assert "nexus-{timestamp}.zip" in backup
    assert "StandardOutput=append:/var/log/nexus/backup.log" in backup
    assert "StandardError=append:/var/log/nexus/backup.log" in backup


def test_launchd_backup_matches_sync_runtime_and_user_log_dir() -> None:
    with (ROOT / "deploy/com.nexus.pipeline.plist").open("rb") as handle:
        sync = plistlib.load(handle)
    with (ROOT / "deploy/com.nexus.backup.plist").open("rb") as handle:
        backup = plistlib.load(handle)

    data_dir = "/Users/you/Library/Application Support/Nexus"
    installed_python = f"{data_dir}/venv/bin/python"
    assert sync["ProgramArguments"][0] == installed_python
    assert backup["ProgramArguments"][0] == installed_python
    assert sync["WorkingDirectory"] == data_dir
    assert backup["WorkingDirectory"] == data_dir
    assert "PYTHONPATH" not in sync["EnvironmentVariables"]
    assert "PYTHONPATH" not in backup["EnvironmentVariables"]
    assert sync["EnvironmentVariables"]["NEXUS_DATA_DIR"] == data_dir
    assert backup["EnvironmentVariables"]["NEXUS_DATA_DIR"] == data_dir
    assert sync["Umask"] == 0o77
    assert backup["Umask"] == 0o77
    arguments = backup["ProgramArguments"]
    assert arguments[arguments.index("--sqlite-path") + 1] == f"{data_dir}/nexus.db"
    assert arguments[arguments.index("--db-dir") + 1] == f"{data_dir}/chroma"
    assert arguments[arguments.index("--output") + 1].startswith(f"{data_dir}/backups/")
    assert arguments[arguments.index("--output") + 1].endswith("nexus-{timestamp}.zip")
    assert backup["StandardOutPath"] == "/Users/you/Library/Logs/Nexus/backup.log"
    assert backup["StandardErrorPath"] == backup["StandardOutPath"]
