"""
Structured logging configuration — Phase 4E-1.

Provides JSON log formatting with call-site metadata and sensitive-data masking.
Import and call ``setup_logging()`` at application entry points.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone


class _JSONFormatter(logging.Formatter):
    """Format log records as one-line JSON objects with structured fields."""

    def __init__(self) -> None:
        super().__init__()
        self._sensitive_re = re.compile(
            r"(api_key|token|password|secret|credential)s?=([\w\-\.]+)",
            re.IGNORECASE,
        )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "call_site": f"{record.filename}:{record.lineno}",
        }
        if record.exc_info and record.exc_info[1]:
            payload["exc"] = str(record.exc_info[1])
        if record.stack_info:
            payload["stack"] = str(record.stack_info)

        line = json.dumps(payload, ensure_ascii=False, default=str)
        return self._sensitive_re.sub(r"\1=***REDACTED***", line)


def setup_logging(
    level: int | str | None = None,
    json_output: bool = True,
) -> None:
    """Configure the root logger for structured output.

    Args:
        level: Log level (default: INFO, or DEBUG if NEXUS_LOG_LEVEL env var is set).
        json_output: If True (default), use JSON formatting; else plain text.
    """
    if level is None:
        env_level = os.getenv("NEXUS_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, env_level, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if json_output:
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Convenience: get a logger for a module."""
    return logging.getLogger(name)
