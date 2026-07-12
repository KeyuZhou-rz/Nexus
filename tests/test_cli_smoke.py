from __future__ import annotations

import subprocess
import sys


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
    assert "ui" in result.stdout


def test_unified_cli_dispatches_help() -> None:
    result = _run("-m", "nexus.cli.main", "doctor", "--help")
    assert result.returncode == 0
    assert "--no-google" in result.stdout


def test_unified_cli_dispatches_db_check_help() -> None:
    result = _run("-m", "nexus.cli.main", "db-check", "--help")
    assert result.returncode == 0
    assert "--json" in result.stdout


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


def test_existing_module_entrypoints_have_help() -> None:
    for module in ("pipeline", "briefing", "ingest", "query"):
        result = _run("-m", f"nexus.cli.{module}", "--help")
        assert result.returncode == 0, (module, result.stderr)
