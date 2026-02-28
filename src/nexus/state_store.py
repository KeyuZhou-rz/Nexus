from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json

STATE_SCHEMA_VERSION = "1.0"


@dataclass
class LearnerState:
    major: str = ""
    current_focus: str = ""
    weak_points: list[str] = field(default_factory=list)
    review_queue: list[str] = field(default_factory=list)
    mastery: dict[str, float] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "major": self.major,
            "current_focus": self.current_focus,
            "weak_points": self.weak_points,
            "review_queue": self.review_queue,
            "mastery": self.mastery,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LearnerState":
        return cls(
            major=str(payload.get("major", "")),
            current_focus=str(payload.get("current_focus", "")),
            weak_points=list(payload.get("weak_points", [])),
            review_queue=list(payload.get("review_queue", [])),
            mastery=dict(payload.get("mastery", {})),
            updated_at=str(payload.get("updated_at", datetime.now().astimezone().isoformat())),
        )


def load_state(path: Path) -> LearnerState:
    if not path.exists():
        return LearnerState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return LearnerState.from_dict(payload)


def save_state(path: Path, state: LearnerState) -> None:
    atomic_write_json(path, state.to_dict(), ensure_ascii=False)
