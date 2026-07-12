"""Unified ``nexus`` command line entry point."""
from __future__ import annotations

import sys


COMMANDS = {
    "doctor": ("nexus.cli.doctor", "Run environment checks"),
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
    "sessions": ("nexus.cli.sessions", "Finalize idle learning sessions"),
    "ui": ("nexus.cli.ui", "Run the Streamlit application"),
    "memory-extract": ("nexus.cli.memory_extract", "Extract weak points from conversations"),
    "memory-feedback": ("nexus.cli.memory_feedback", "Apply feedback to a weak point"),
    "memory-query": ("nexus.cli.memory_query", "Query conversation memory"),
}


def _help() -> None:
    print("Usage: nexus <command> [options]\n")
    print("Commands:")
    for name, (_, description) in COMMANDS.items():
        print(f"  {name:<16} {description}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        _help()
        return
    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"Unknown command: {command}\n")
        _help()
        raise SystemExit(2)

    module_name, _ = COMMANDS[command]
    module = __import__(module_name, fromlist=["main"])
    sys.argv = [f"nexus {command}", *sys.argv[2:]]
    module.main()


if __name__ == "__main__":
    main()
