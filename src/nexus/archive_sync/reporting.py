from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..io_utils import atomic_write_json


def build_failure_report(failures: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "failure_count": len(failures),
        "failures": failures,
    }


def save_failure_report(path: Path, failures: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, build_failure_report(failures), ensure_ascii=False)
