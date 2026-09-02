"""V1 decision constants. Not user settings; covered by tests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

V1_TIMEZONE_NAME = "America/New_York"
V1_TZ = ZoneInfo(V1_TIMEZONE_NAME)
# Monday 2026-09-07 09:00 local — frozen clock for V1 unit/integration tests.
V1_NOW = datetime(2026, 9, 7, 9, 0, tzinfo=V1_TZ)

INACTIVE_OBLIGATION_STATUSES = frozenset({"done", "completed", "cancelled"})
ACTIVE_OBLIGATION_STATUSES = frozenset({"open", "in_progress"})
RISK_BANDS = ("overdue", "high", "medium", "low", "unscored")
RISK_SEVERITY = {
    "overdue": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "unscored": 4,
}


@dataclass(frozen=True)
class RiskThresholds:
    high_deadline_hours: float = 24.0
    high_load_ratio: float = 0.60
    medium_load_ratio: float = 0.50
    medium_deadline_hours: float = 72.0
    planning_horizon_days: int = 14
    max_block_minutes: int = 50
    transition_buffer_minutes: int = 15
    end_of_day_hour: int = 23
    end_of_day_minute: int = 59


V1_THRESHOLDS = RiskThresholds()
