"""Availability-aware obligation risk. Pure; no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from ..availability import CapacityResult, normalize_due_at
from ..v1_config import (
    INACTIVE_OBLIGATION_STATUSES,
    V1_THRESHOLDS,
    V1_TZ,
    RiskThresholds,
)

_REASON_ORDER = (
    "inactive",
    "overdue",
    "no_deadline",
    "no_remaining_estimate",
    "availability_unknown",
    "remaining_exceeds_available",
    "deadline_within_24h",
    "load_ratio_high",
    "load_ratio_medium",
    "deadline_within_72h",
    "calendar_conflict",
    "within_horizon",
)


@dataclass(frozen=True)
class EffortEstimate:
    remaining_hours: float | None
    estimated_hours: float | None
    spent_hours: float
    basis: str  # 'manual' | 'history' | 'type_prior'


@dataclass(frozen=True)
class RiskAssessment:
    obligation_id: str
    band: str
    remaining_hours: float | None
    remaining_basis: str
    available_hours: float | None
    availability_reason: str | None
    load_ratio: float | None
    overload_gap: float
    reasons: tuple[str, ...]
    computed_at: datetime
    state_revision: int
    due_at: datetime | None = None


def _ordered_reasons(codes: set[str]) -> tuple[str, ...]:
    return tuple(code for code in _REASON_ORDER if code in codes)


def _load_fields(
    remaining_hours: float | None,
    available_hours: float | None,
) -> tuple[float | None, float]:
    if remaining_hours is None or available_hours is None:
        return None, 0.0
    load_ratio = remaining_hours / available_hours if available_hours > 0 else None
    return load_ratio, max(remaining_hours - available_hours, 0.0)


def estimate_remaining(
    *,
    estimated_hours: float | None,
    spent_hours: float,
    remaining_override_hours: float | None,
    history_hours: float | None,
    type_prior_hours: float,
    status: str,
) -> EffortEstimate:
    if remaining_override_hours is not None:
        basis = "manual"
        effective = remaining_override_hours
        remaining = max(remaining_override_hours, 0.0)
    elif estimated_hours is not None:
        basis = "manual"
        effective = estimated_hours
        remaining = max(estimated_hours - spent_hours, 0.0)
    elif history_hours is not None:
        basis = "history"
        effective = history_hours
        remaining = max(history_hours - spent_hours, 0.0)
    else:
        basis = "type_prior"
        effective = type_prior_hours
        remaining = max(type_prior_hours - spent_hours, 0.0)
    if status in INACTIVE_OBLIGATION_STATUSES:
        remaining = 0.0
    return EffortEstimate(
        remaining_hours=remaining,
        estimated_hours=effective,
        spent_hours=spent_hours,
        basis=basis,
    )


def evaluate_obligation_risk(
    *,
    obligation_id: str,
    due_at: datetime | None,
    status: str,
    remaining_hours: float | None,
    remaining_basis: str,
    capacity: CapacityResult,
    now: datetime,
    state_revision: int = 0,
    thresholds: RiskThresholds = V1_THRESHOLDS,
) -> RiskAssessment:
    zone: tzinfo | None = now.tzinfo or V1_TZ
    due = normalize_due_at(due_at, tz=zone)
    available_hours = capacity.available_hours
    availability_reason = capacity.reason
    load_ratio, overload_gap = _load_fields(remaining_hours, available_hours)
    conflict = bool(capacity.calendar_conflict)

    def _result(band: str, reasons: set[str]) -> RiskAssessment:
        return RiskAssessment(
            obligation_id=obligation_id,
            band=band,
            remaining_hours=remaining_hours if status not in INACTIVE_OBLIGATION_STATUSES else 0.0,
            remaining_basis=remaining_basis,
            available_hours=available_hours,
            availability_reason=availability_reason,
            load_ratio=load_ratio,
            overload_gap=overload_gap,
            reasons=_ordered_reasons(reasons),
            computed_at=now,
            state_revision=state_revision,
            due_at=due,
        )

    if status in INACTIVE_OBLIGATION_STATUSES:
        return _result("unscored", {"inactive"})

    if due is not None and due < now:
        reasons = {"overdue"}
        if conflict:
            reasons.add("calendar_conflict")
        return _result("overdue", reasons)

    if due is None:
        return _result("unscored", {"no_deadline"})
    if remaining_hours is None:
        return _result("unscored", {"no_remaining_estimate"})
    if available_hours is None:
        return _result("unscored", {"availability_unknown"})

    delta = due - now
    within_24 = timedelta(0) <= delta <= timedelta(hours=thresholds.high_deadline_hours)
    within_72 = timedelta(0) <= delta <= timedelta(hours=thresholds.medium_deadline_hours)
    within_horizon = timedelta(0) <= delta <= timedelta(days=thresholds.planning_horizon_days)

    high_reasons: set[str] = set()
    if remaining_hours > available_hours:
        high_reasons.add("remaining_exceeds_available")
    if (
        within_24
        and load_ratio is not None
        and load_ratio >= thresholds.high_load_ratio
    ):
        high_reasons.add("deadline_within_24h")
        high_reasons.add("load_ratio_high")
    if high_reasons:
        if conflict:
            high_reasons.add("calendar_conflict")
        return _result("high", high_reasons)

    medium_reasons: set[str] = set()
    if load_ratio is not None and load_ratio >= thresholds.medium_load_ratio:
        medium_reasons.add("load_ratio_medium")
    if within_72 and remaining_hours > 0:
        medium_reasons.add("deadline_within_72h")
    if medium_reasons:
        if conflict:
            medium_reasons.add("calendar_conflict")
        return _result("medium", medium_reasons)

    if within_horizon:
        low_reasons = {"within_horizon"}
        if conflict:
            low_reasons.add("calendar_conflict")
        return _result("low", low_reasons)

    return _result("unscored", set())
