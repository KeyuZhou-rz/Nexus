from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json

STATE_SCHEMA_VERSION = "1.0"


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class LearnerState:
    major: str = ""
    current_focus: str = ""
    weak_points: list[str] = field(default_factory=list)
    review_queue: list[str] = field(default_factory=list)
    mastery: dict[str, float] = field(default_factory=dict)
    weak_point_evidence: dict[str, list[str]] = field(default_factory=dict)
    weak_point_confidence: dict[str, float] = field(default_factory=dict)
    weak_point_status: dict[str, str] = field(default_factory=dict)
    corrections: list[dict[str, str]] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "major": self.major,
            "current_focus": self.current_focus,
            "weak_points": self.weak_points,
            "review_queue": self.review_queue,
            "mastery": self.mastery,
            "weak_point_evidence": self.weak_point_evidence,
            "weak_point_confidence": self.weak_point_confidence,
            "weak_point_status": self.weak_point_status,
            "corrections": self.corrections,
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
            weak_point_evidence={
                str(k): [str(item) for item in list(v)]
                for k, v in dict(payload.get("weak_point_evidence", {})).items()
            },
            weak_point_confidence={
                str(k): _safe_float(v)
                for k, v in dict(payload.get("weak_point_confidence", {})).items()
            },
            weak_point_status={
                str(k): str(v)
                for k, v in dict(payload.get("weak_point_status", {})).items()
            },
            corrections=[
                {
                    "topic": str(item.get("topic", "")),
                    "action": str(item.get("action", "")),
                    "timestamp": str(item.get("timestamp", "")),
                }
                for item in list(payload.get("corrections", []))
                if isinstance(item, dict)
            ],
            updated_at=str(payload.get("updated_at", datetime.now().astimezone().isoformat())),
        )


def load_state(path: Path) -> LearnerState:
    if not path.exists():
        return LearnerState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return LearnerState.from_dict(payload)


def save_state(path: Path, state: LearnerState) -> None:
    atomic_write_json(path, state.to_dict(), ensure_ascii=False)
