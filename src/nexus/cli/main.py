"""Unified ``nexus`` command line entry point."""
from __future__ import annotations

import sys


COMMANDS = {
    "next": ("nexus.cli.next_action", "Show the one task to do now, without refreshing"),
    "start": ("nexus.cli.act:start", "Start timing the current task (or one matching a keyword)"),
    "pause": ("nexus.cli.act:pause", "Pause the running task"),
    "done": ("nexus.cli.act:done", "Mark the current task (or one matching a keyword) complete"),
    "add": ("nexus.cli.act:add", "Add a task the course calendar does not have"),
    "week": ("nexus.cli.week", "Show the last seven days: use, recommendations followed, estimates"),
    "dashboard": ("nexus.cli.dashboard", "Open the full task list in the browser"),
    "sync": ("nexus.cli.sync", "Refresh course calendars now"),
    "init": ("nexus.cli.init", "Initialize the runtime and connect a course calendar feed"),
    "sync-status": ("nexus.cli.sync_status", "Show read-only source synchronization health"),
    "context": ("nexus.cli.context", "Export the current state and next action as JSON"),
    "demo": ("nexus.cli.demo", "Create an isolated synthetic demo runtime"),
    "doctor": ("nexus.cli.doctor", "Run environment checks"),
    "status": ("nexus.cli.status", "Show read-only runtime status"),
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
}
# Everything after ``context`` in COMMANDS belongs to frozen extensions.
CORE_COMMANDS = tuple(COMMANDS)[: tuple(COMMANDS).index("context") + 1]


def _help() -> None:
    print("Usage: nexus [command] [options]\n")
    print(f"  {'(no command)':<16} Refresh stale course calendars, then show the one task to do now")
    for name in CORE_COMMANDS:
        print(f"  {name:<16} {COMMANDS[name][1]}")
    print("\nMaintenance and frozen extensions (see docs/ROADMAP.md):")
    for name, (_, description) in COMMANDS.items():
        if name not in CORE_COMMANDS:
            print(f"  {name:<16} {description}")


def _run(target: str, command: str, argv: list[str]) -> int:
    module_name, _, attr = target.partition(":")
    from nexus.log_config import setup_logging

    setup_logging()
    module = __import__(module_name, fromlist=[attr or "main"])
    sys.argv = [f"nexus {command}".rstrip(), *argv]
    result = getattr(module, attr or "main")()
    return int(result) if result is not None else 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1].startswith("--") and sys.argv[1] != "--help":
        return _run("nexus.cli.next_action:home", "", sys.argv[1:])
    if sys.argv[1] in {"-h", "--help", "help"}:
        _help()
        return 0
    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"Unknown command: {command}\n")
        _help()
        raise SystemExit(2)
    return _run(COMMANDS[command][0], command, sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
