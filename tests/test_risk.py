from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from nexus.core.availability import compute_capacity
from nexus.core.risk import estimate_remaining, evaluate_obligation_risk
from nexus.core.v1_config import V1_NOW, V1_THRESHOLDS
from tests.fixtures.v1_loop import (
    conflict_block_leaving_two_hours,
    cs_due_moved,
    cs_due_original,
    twelve_hour_rules,
)

_RISK_SOURCE = Path(__file__).resolve().parents[1] / "src" / "nexus" / "core" / "risk.py"


def _capacity(*, due_at, rules=None, calendar_blocks=None):
    return compute_capacity(
        now=V1_NOW,
        due_at=due_at,
        rules=twelve_hour_rules() if rules is None else rules,
        overrides=[],
        calendar_blocks=[] if calendar_blocks is None else calendar_blocks,
    )


def _risk(**kwargs):
    defaults = {
        "obligation_id": "cs-assignment-2",
        "due_at": cs_due_original(),
        "status": "open",
        "remaining_hours": 6.0,
        "remaining_basis": "manual",
        "capacity": _capacity(due_at=cs_due_original()),
        "now": V1_NOW,
        "state_revision": 1,
        "thresholds": V1_THRESHOLDS,
    }
    defaults.update(kwargs)
    return evaluate_obligation_risk(**defaults)


def test_six_hours_remaining_twelve_available_is_medium_not_high() -> None:
    capacity = _capacity(due_at=cs_due_original())
    assert capacity.available_hours is not None
    assert abs(capacity.available_hours - 12.0) < 1e-6
    assessment = _risk(capacity=capacity, due_at=cs_due_original(), remaining_hours=6.0)
    assert assessment.band == "medium"
    assert assessment.band != "high"
    assert assessment.load_ratio == 0.5
    assert assessment.overload_gap == 0.0
    assert assessment.reasons == ("load_ratio_medium",)
    assert assessment.available_hours == capacity.available_hours
    assert assessment.computed_at == V1_NOW
    assert assessment.state_revision == 1


def test_due_moved_with_two_hours_available_is_high() -> None:
    capacity = _capacity(
        due_at=cs_due_moved(),
        calendar_blocks=[conflict_block_leaving_two_hours()],
    )
    assert capacity.available_hours is not None
    assert abs(capacity.available_hours - 2.0) < 1e-6
    assessment = _risk(
        due_at=cs_due_moved(),
        remaining_hours=6.0,
        capacity=capacity,
    )
    assert assessment.band == "high"
    assert "remaining_exceeds_available" in assessment.reasons
    assert assessment.overload_gap == 4.0
    assert assessment.load_ratio == 3.0
    assert "calendar_conflict" in assessment.reasons
    assert assessment.reasons == (
        "remaining_exceeds_available",
        "deadline_within_24h",
        "load_ratio_high",
        "calendar_conflict",
    )


def test_no_availability_is_unscored_not_high() -> None:
    capacity = _capacity(due_at=cs_due_moved(), rules=[])
    assert capacity.available_hours is None
    assert capacity.reason == "availability_unknown"
    assessment = _risk(
        due_at=cs_due_moved(),
        remaining_hours=6.0,
        capacity=capacity,
    )
    assert assessment.band == "unscored"
    assert assessment.band != "high"
    assert assessment.availability_reason == "availability_unknown"
    assert assessment.reasons == ("availability_unknown",)
    assert assessment.load_ratio is None
    assert assessment.overload_gap == 0.0


def test_active_overdue_wins_regardless_of_availability() -> None:
    due_at = V1_NOW - timedelta(hours=1)
    unknown = _capacity(due_at=cs_due_original(), rules=[])
    assessment = _risk(due_at=due_at, remaining_hours=6.0, capacity=unknown)
    assert assessment.band == "overdue"
    assert assessment.reasons[0] == "overdue"
    known = _capacity(due_at=cs_due_original())
    also = _risk(due_at=due_at, remaining_hours=None, capacity=known)
    assert also.band == "overdue"


def test_inactive_completed_is_not_overdue_or_high() -> None:
    due_at = V1_NOW - timedelta(hours=2)
    capacity = _capacity(due_at=cs_due_moved(), calendar_blocks=[conflict_block_leaving_two_hours()])
    for status in ("completed", "done", "cancelled"):
        assessment = _risk(
            status=status,
            due_at=due_at,
            remaining_hours=6.0,
            capacity=capacity,
        )
        assert assessment.band == "unscored"
        assert assessment.band not in {"overdue", "high"}
        assert assessment.reasons == ("inactive",)


def test_remaining_none_is_unscored() -> None:
    assessment = _risk(remaining_hours=None, due_at=cs_due_original())
    assert assessment.band == "unscored"
    assert assessment.reasons == ("no_remaining_estimate",)


def test_no_deadline_is_unscored() -> None:
    assessment = _risk(due_at=None, remaining_hours=6.0)
    assert assessment.band == "unscored"
    assert assessment.reasons == ("no_deadline",)


def test_module_source_has_no_priority() -> None:
    source = _RISK_SOURCE.read_text(encoding="utf-8")
    assert "priority" not in source.lower()
    signature = inspect.signature(evaluate_obligation_risk)
    assert "priority" not in signature.parameters


def test_load_ratio_and_overload_gap_numeric_fields() -> None:
    even = _risk(remaining_hours=6.0)
    assert isinstance(even.load_ratio, float)
    assert isinstance(even.overload_gap, float)
    assert even.load_ratio == 0.5
    assert even.overload_gap == 0.0
    high = _risk(
        due_at=cs_due_moved(),
        remaining_hours=6.0,
        capacity=_capacity(
            due_at=cs_due_moved(),
            calendar_blocks=[conflict_block_leaving_two_hours()],
        ),
    )
    assert high.load_ratio == 3.0
    assert high.overload_gap == 4.0


def test_reasons_are_ordered_and_machine_readable() -> None:
    assessment = _risk(
        due_at=cs_due_moved(),
        remaining_hours=6.0,
        capacity=_capacity(
            due_at=cs_due_moved(),
            calendar_blocks=[conflict_block_leaving_two_hours()],
        ),
    )
    allowed = (
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
    assert assessment.reasons
    assert all(reason in allowed for reason in assessment.reasons)
    order = {reason: index for index, reason in enumerate(allowed)}
    indexes = [order[reason] for reason in assessment.reasons]
    assert indexes == sorted(indexes)


def test_manual_remaining_override_wins_vs_type_prior() -> None:
    overridden = estimate_remaining(
        estimated_hours=10.0,
        spent_hours=2.0,
        remaining_override_hours=3.0,
        history_hours=8.0,
        type_prior_hours=4.0,
        status="open",
    )
    assert overridden.remaining_hours == 3.0
    assert overridden.basis == "manual"

    from_estimate = estimate_remaining(
        estimated_hours=10.0,
        spent_hours=2.0,
        remaining_override_hours=None,
        history_hours=8.0,
        type_prior_hours=4.0,
        status="open",
    )
    assert from_estimate.remaining_hours == 8.0
    assert from_estimate.basis == "manual"

    from_history = estimate_remaining(
        estimated_hours=None,
        spent_hours=2.0,
        remaining_override_hours=None,
        history_hours=8.0,
        type_prior_hours=4.0,
        status="open",
    )
    assert from_history.remaining_hours == 6.0
    assert from_history.basis == "history"

    from_prior = estimate_remaining(
        estimated_hours=None,
        spent_hours=2.0,
        remaining_override_hours=None,
        history_hours=None,
        type_prior_hours=4.0,
        status="open",
    )
    assert from_prior.remaining_hours == 2.0
    assert from_prior.basis == "type_prior"

    inactive = estimate_remaining(
        estimated_hours=None,
        spent_hours=2.0,
        remaining_override_hours=None,
        history_hours=None,
        type_prior_hours=4.0,
        status="completed",
    )
    assert inactive.remaining_hours == 0.0
    assert inactive.basis == "type_prior"


def test_thresholds_come_from_v1_thresholds() -> None:
    signature = inspect.signature(evaluate_obligation_risk)
    assert signature.parameters["thresholds"].default is V1_THRESHOLDS
    due_at = cs_due_moved()
    capacity = _capacity(
        due_at=due_at,
        calendar_blocks=[conflict_block_leaving_two_hours()],
    )
    default = _risk(
        due_at=due_at,
        remaining_hours=1.2,
        capacity=capacity,
    )
    assert default.band == "high"
    assert "load_ratio_high" in default.reasons
    custom = _risk(
        due_at=due_at,
        remaining_hours=1.2,
        capacity=capacity,
        thresholds=replace(V1_THRESHOLDS, high_load_ratio=0.99),
    )
    assert custom.band == "medium"
    assert "deadline_within_72h" in custom.reasons


def test_remaining_zero_within_horizon_is_low() -> None:
    assessment = _risk(remaining_hours=0.0, due_at=cs_due_original())
    assert assessment.band == "low"
    assert assessment.reasons == ("within_horizon",)
