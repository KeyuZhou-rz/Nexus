from __future__ import annotations

import subprocess
import sys
import json
import os


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_unified_cli_help() -> None:
    result = _run("-m", "nexus.cli.main", "--help")
    assert result.returncode == 0
    assert "pipeline" in result.stdout
    assert "doctor" in result.stdout
    assert "db-check" in result.stdout
    assert "cache" in result.stdout
    assert "dashboard" in result.stdout
    assert "demo" in result.stdout
    assert "sync" in result.stdout


def test_unified_cli_dispatches_help() -> None:
    result = _run("-m", "nexus.cli.main", "doctor", "--help")
    assert result.returncode == 0
    assert "--no-google" in result.stdout


def test_unified_cli_dispatches_db_check_help() -> None:
    result = _run("-m", "nexus.cli.main", "db-check", "--help")
    assert result.returncode == 0
    assert "--json" in result.stdout


def test_unified_cli_dispatches_calibration_help() -> None:
    result = _run("-m", "nexus.cli.main", "calibration", "--help")
    assert result.returncode == 0
    assert "--fixture" in result.stdout


def test_unified_cli_dispatches_context_help() -> None:
    result = _run("-m", "nexus.cli.main", "context", "--help")
    assert result.returncode == 0
    assert "--course-id" in result.stdout


def test_unified_cli_dispatches_next_help() -> None:
    result = _run("-m", "nexus.cli.main", "next", "--help")
    assert result.returncode == 0
    assert "--data-dir" in result.stdout


def test_unified_cli_dispatches_sync_status_help() -> None:
    result = _run("-m", "nexus.cli.main", "sync-status", "--help")
    assert result.returncode == 0
    assert "--data-dir" in result.stdout


def test_unified_cli_dispatches_sync_help() -> None:
    result = _run("-m", "nexus.cli.main", "sync", "--help")
    assert result.returncode == 0
    assert "--include-archive" in result.stdout
    assert "--google" in result.stdout


def test_unified_cli_dispatches_dashboard_help() -> None:
    result = _run("-m", "nexus.cli.main", "dashboard", "--help")
    assert result.returncode == 0
    assert "streamlit" in result.stdout.lower()


def test_init_command_creates_runtime_and_feed(tmp_path) -> None:
    result = _run(
        "-m", "nexus.cli.main", "init",
        "--data-dir", str(tmp_path),
        "--feed-url", "https://example.edu/feed.ics",
        "--feed-name", "Operating Systems",
        "--course", "CS202_OS",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["feed"]["action"] == "created"
    assert json.loads((tmp_path / "feeds.json").read_text(encoding="utf-8"))[0]["course"] == "CS202_OS"


def test_demo_command_creates_complete_runtime(tmp_path) -> None:
    data_dir = tmp_path / "demo"
    result = _run(
        "-m", "nexus.cli.main", "demo", "--data-dir", str(data_dir), "--json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["synthetic"] is True
    assert payload["task_count"] == 4
    assert payload["evidence_count"] == 1


def test_next_command_prints_and_persists_judgment(tmp_path) -> None:
    data_dir = tmp_path / "demo"
    created = _run("-m", "nexus.cli.main", "demo", "--data-dir", str(data_dir), "--json")
    assert created.returncode == 0, created.stderr
    result = _run("-m", "nexus.cli.main", "next", "--data-dir", str(data_dir), "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["available"] is True
    assert payload["suggestion"]
    saved = json.loads((data_dir / "judgment.json").read_text(encoding="utf-8"))
    assert saved["task_id"] == payload["task_id"]


def test_sync_status_reports_isolated_empty_runtime(tmp_path) -> None:
    result = _run(
        "-m", "nexus.cli.main", "sync-status", "--data-dir", str(tmp_path), "--json",
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["failed_sources"] == 0
    assert payload["sources"]["google"]["status"] == "disabled"


def test_unified_cli_propagates_subcommand_failure(tmp_path) -> None:
    result = _run(
        "-m",
        "nexus.cli.main",
        "db-check",
        "--sqlite-path",
        str(tmp_path / "missing.db"),
        "--db-dir",
        str(tmp_path / "missing-chroma"),
        "--json",
    )
    assert result.returncode == 1
    assert '"ok": false' in result.stdout


def test_unified_cli_dispatches_database_maintenance_help() -> None:
    expected = {
        "db-backup": "--output",
        "db-restore": "--apply",
        "db-repair": "--apply",
    }
    for command, option in expected.items():
        result = _run("-m", "nexus.cli.main", command, "--help")
        assert result.returncode == 0, (command, result.stderr)
        assert option in result.stdout


def test_unified_cli_dispatches_cache_help() -> None:
    result = _run("-m", "nexus.cli.main", "cache", "--help")
    assert result.returncode == 0
    assert "list" in result.stdout
    assert "clear" in result.stdout


def test_unified_cli_dispatches_mastery_migration_help() -> None:
    result = _run("-m", "nexus.cli.main", "mastery-migrate", "--help")
    assert result.returncode == 0
    assert "--apply" in result.stdout


def test_existing_module_entrypoints_have_help() -> None:
    for module in ("pipeline", "briefing", "ingest", "query"):
        result = _run("-m", f"nexus.cli.{module}", "--help")
        assert result.returncode == 0, (module, result.stderr)


def test_pipeline_exit_code_tracks_structured_report(tmp_path) -> None:
    env = {**os.environ, "NEXUS_DATA_DIR": str(tmp_path / "data")}
    ok = subprocess.run(
        [sys.executable, "-m", "nexus.cli.pipeline", "--skip-archive-sync", "--no-google"],
        text=True, capture_output=True, check=False, env=env,
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["ok"] is True

    output_directory = tmp_path / "not-a-file"
    output_directory.mkdir()
    failed = subprocess.run(
        [
            sys.executable, "-m", "nexus.cli.pipeline", "--skip-archive-sync", "--no-google",
            "--briefing-output", str(output_directory),
        ],
        text=True, capture_output=True, check=False, env=env,
    )
    assert failed.returncode == 1
    payload = json.loads(failed.stdout)
    assert payload["ok"] is False
    assert any(step["name"] == "briefing" and step["status"] == "failed" for step in payload["steps"])


def test_db_restore_main_returns_one_on_bad_archive(tmp_path, monkeypatch) -> None:
    from nexus.cli import db_restore
    from nexus.knowledge import maintenance

    def fake_restore_backup(*_args, **_kwargs):
        return {"ok": False, "error": "simulated failure"}

    monkeypatch.setattr(maintenance, "restore_backup", fake_restore_backup)
    monkeypatch.setattr(sys, "argv", ["db-restore", str(tmp_path / "missing.zip")])
    assert db_restore.main() == 1


def test_db_backup_main_returns_one_when_create_backup_fails(tmp_path, monkeypatch) -> None:
    from nexus.cli import db_backup
    from nexus.knowledge import maintenance

    def fake_create_backup(*_args, **_kwargs):
        return {"ok": False, "error": "simulated failure"}

    monkeypatch.setattr(maintenance, "create_backup", fake_create_backup)
    monkeypatch.setattr(sys, "argv", ["db-backup", "--sqlite-path", str(tmp_path / "nexus.db")])
    assert db_backup.main() == 1


def test_new_dispatcher_commands_are_importable() -> None:
    import importlib

    from nexus.cli.main import COMMANDS

    for name in ("concepts-backfill", "retro", "scan", "sprint", "next"):
        assert name in COMMANDS, f"{name} missing from COMMANDS"
        module_name, _ = COMMANDS[name]
        module = importlib.import_module(module_name)
        assert hasattr(module, "main"), f"{module_name} has no main"


def test_memory_extract_feedback_expose_course_id() -> None:
    for module in ("memory-extract", "memory-feedback"):
        result = _run("-m", f"nexus.cli.{module.replace('-', '_')}", "--help")
        assert result.returncode == 0, (module, result.stderr)
        assert "--course-id" in result.stdout, f"{module} missing --course-id option"
