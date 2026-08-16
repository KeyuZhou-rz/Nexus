"""Unified ``nexus`` command line entry point."""
from __future__ import annotations

import sys


COMMANDS = {
    "init": ("nexus.cli.init", "Initialize the local runtime and optional LMS feed"),
    "demo": ("nexus.cli.demo", "Create an isolated synthetic demo runtime"),
    "dashboard": ("nexus.cli.dashboard", "Launch the local information dashboard"),
    "doctor": ("nexus.cli.doctor", "Run environment checks"),
    "status": ("nexus.cli.status", "Show read-only runtime status"),
    "sync-status": ("nexus.cli.sync_status", "Show read-only source synchronization health"),
    "sync": ("nexus.cli.sync", "Run one safe headless synchronization cycle"),
    "pipeline": ("nexus.cli.pipeline", "Run the integrated task pipeline"),
    "briefing": ("nexus.cli.briefing", "Generate a daily briefing"),
    "ingest": ("nexus.cli.ingest", "Ingest local course documents"),
    "query": ("nexus.cli.query", "Query the knowledge store"),
    "read": ("nexus.cli.read", "Read stored knowledge chunks"),
    "db-check": ("nexus.cli.db_check", "Audit SQLite and Chroma consistency"),
    "db-backup": ("nexus.cli.db_backup", "Create a database and index backup"),
    "db-restore": ("nexus.cli.db_restore", "Validate or restore a backup"),
    "db-repair": ("nexus.cli.db_repair", "Preview or apply conservative repairs"),
    "cache": ("nexus.cli.cache", "List or clear parser cache entries"),
    "mastery-migrate": ("nexus.cli.mastery_migrate", "Migrate legacy mastery evidence"),
    "sessions": ("nexus.cli.sessions", "Finalize idle learning sessions"),
    "memory-extract": ("nexus.cli.memory_extract", "Extract weak points from conversations"),
    "memory-feedback": ("nexus.cli.memory_feedback", "Apply feedback to a weak point"),
    "memory-query": ("nexus.cli.memory_query", "Query conversation memory"),
    "concepts-backfill": ("nexus.cli.concepts_backfill", "Backfill concept metadata on existing chunks"),
    "retro": ("nexus.cli.retro", "Generate an exam retrospective"),
    "scan": ("nexus.cli.scan", "Scan a course for knowledge gaps"),
    "sprint": ("nexus.cli.sprint", "Generate an exam sprint plan"),
    "calibration": ("nexus.cli.calibration", "Evaluate an offline assessment calibration fixture"),
    "context": ("nexus.cli.context", "Export the current learning context"),
}


def _help() -> None:
    print("Usage: nexus <command> [options]\n")
    print("Commands:")
    for name, (_, description) in COMMANDS.items():
        print(f"  {name:<16} {description}")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        _help()
        return 0
    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"Unknown command: {command}\n")
        _help()
        raise SystemExit(2)

    module_name, _ = COMMANDS[command]
    from nexus.log_config import setup_logging

    setup_logging()
    module = __import__(module_name, fromlist=["main"])
    sys.argv = [f"nexus {command}", *sys.argv[2:]]
    result = module.main()
    return int(result) if result is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
