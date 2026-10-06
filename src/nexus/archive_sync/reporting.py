"""Backward-compatible archive imports for the shared failure queue."""
from __future__ import annotations

from ..core.failure_queue import (
    DEFAULT_MAX_RETRIES,
    build_failure_report,
    load_failure_queue,
    save_failure_report,
    update_failure_queue,
)

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "build_failure_report",
    "load_failure_queue",
    "save_failure_report",
    "update_failure_queue",
]
