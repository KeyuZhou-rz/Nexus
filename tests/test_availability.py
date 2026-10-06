from __future__ import annotations

from datetime import date, datetime, timezone

from nexus.core.availability import (
    AvailabilityOverride,
    AvailabilityRule,
    CalendarBlock,
    compute_capacity,
    has_availability_input,
    normalize_due_at,
)
from nexus.core.v1_config import V1_NOW, V1_THRESHOLDS, V1_TZ
from tests.fixtures.v1_loop import (
    conflict_block_leaving_two_hours,
    cs_due_moved,
    cs_due_original,
    twelve_hour_rules,
)

UTC = timezone.utc


def _capacity(
    *,
    due_at=None,
    rules=None,
    overrides=None,
    calendar_blocks=None,
    now=None,
    tz=None,
):
    return compute_capacity(
        now=now if now is not None else V1_NOW,
        due_at=due_at if due_at is not None else cs_due_original(),
        rules=twelve_hour_rules() if rules is None else rules,
        overrides=[] if overrides is None else overrides,
        calendar_blocks=[] if calendar_blocks is None else calendar_blocks,
        tz=tz,
    )


def test_twelve_hour_rules_until_next_monday() -> None:
    result = _capacity(due_at=cs_due_original())
    assert result.reason is None
    assert result.available_hours is not None
    assert abs(result.available_hours - 12.0) < 1e-6
    assert abs(result.contiguous_minutes - 240.0) < 1e-6
    assert result.calendar_conflict is False


def test_conflict_block_leaves_two_hours_before_tuesday() -> None:
    result = _capacity(
        due_at=cs_due_moved(),
        calendar_blocks=[conflict_block_leaving_two_hours()],
    )
    assert result.available_hours is not None
    assert abs(result.available_hours - 2.0) < 1e-6
    assert abs(result.contiguous_minutes - 120.0) < 1e-6
    assert result.calendar_conflict is True
    assert len(result.intervals) == 1
    start, end = result.intervals[0]
    assert start == datetime(2026, 9, 7, 14, 0, tzinfo=V1_TZ)
    assert end == datetime(2026, 9, 7, 16, 0, tzinfo=V1_TZ)


def test_no_rules_or_overrides_is_unknown_not_zero() -> None:
    result = _capacity(rules=[], overrides=[])
    assert result.available_hours is None
    assert result.reason == "availability_unknown"
    assert result.available_hours != 0
    assert result.contiguous_minutes == 0.0
    assert result.intervals == ()


def test_unavailable_only_is_unknown() -> None:
    override = AvailabilityOverride(
        id="blocked",
        starts_at=datetime(2026, 9, 7, 14, 0, tzinfo=V1_TZ),
        ends_at=datetime(2026, 9, 7, 18, 0, tzinfo=V1_TZ),
        kind="unavailable",
    )
    assert has_availability_input([], [override]) is False
    result = _capacity(rules=[], overrides=[override])
    assert result.available_hours is None
    assert result.reason == "availability_unknown"


def test_overlapping_windows_union_not_sum() -> None:
    rules = [
        AvailabilityRule(
            id="a",
            weekday=0,
            starts_local="14:00",
            ends_local="18:00",
            timezone="America/New_York",
        ),
        AvailabilityRule(
            id="b",
            weekday=0,
            starts_local="16:00",
            ends_local="20:00",
            timezone="America/New_York",
        ),
    ]
    due = datetime(2026, 9, 8, 9, 0, tzinfo=V1_TZ)
    result = _capacity(due_at=due, rules=rules)
    assert result.available_hours is not None
    assert abs(result.available_hours - 6.0) < 1e-6


def test_cross_midnight_rule() -> None:
    rules = [
        AvailabilityRule(
            id="wrap",
            weekday=0,
            starts_local="22:00",
            ends_local="02:00",
            timezone="America/New_York",
        )
    ]
    due = datetime(2026, 9, 8, 9, 0, tzinfo=V1_TZ)
    result = _capacity(due_at=due, rules=rules)
    assert result.available_hours is not None
    assert abs(result.available_hours - 4.0) < 1e-6


def test_expired_window_contributes_zero() -> None:
    rules = [
        AvailabilityRule(
            id="past",
            weekday=0,
            starts_local="14:00",
            ends_local="18:00",
            timezone="America/New_York",
        )
    ]
    now = datetime(2026, 9, 7, 19, 0, tzinfo=V1_TZ)
    due = datetime(2026, 9, 8, 9, 0, tzinfo=V1_TZ)
    result = _capacity(now=now, due_at=due, rules=rules)
    assert result.available_hours is not None
    assert abs(result.available_hours - 0.0) < 1e-6
    assert result.contiguous_minutes == 0.0


def test_due_clipping() -> None:
    rules = [
        AvailabilityRule(
            id="mon",
            weekday=0,
            starts_local="14:00",
            ends_local="18:00",
            timezone="America/New_York",
        )
    ]
    due = datetime(2026, 9, 7, 16, 0, tzinfo=V1_TZ)
    result = _capacity(due_at=due, rules=rules)
    assert result.available_hours is not None
    assert abs(result.available_hours - 2.0) < 1e-6


def test_invalid_end_le_start_block_ignored() -> None:
    start = datetime(2026, 9, 7, 16, 0, tzinfo=V1_TZ)
    block = CalendarBlock(
        id="invalid",
        source="gcal",
        external_id="bad",
        title="broken",
        starts_at=start,
        ends_at=start,
    )
    result = _capacity(due_at=cs_due_moved(), calendar_blocks=[block])
    assert result.available_hours is not None
    assert abs(result.available_hours - 4.0) < 1e-6
    assert result.calendar_conflict is False


def test_duplicate_identical_blocks_subtract_once() -> None:
    block = conflict_block_leaving_two_hours()
    duplicate = dict(block)
    duplicate["external_id"] = "conflict-copy"
    result = _capacity(
        due_at=cs_due_moved(),
        calendar_blocks=[block, duplicate],
    )
    assert result.available_hours is not None
    assert abs(result.available_hours - 2.0) < 1e-6
    assert result.calendar_conflict is True


def test_timezone_conversion_utc_block_against_ny_rules() -> None:
    block = CalendarBlock(
        id="utc-busy",
        source="gcal",
        external_id="utc-busy",
        title="UTC meeting",
        starts_at=datetime(2026, 9, 7, 20, 0, tzinfo=UTC),
        ends_at=datetime(2026, 9, 7, 22, 0, tzinfo=UTC),
    )
    result = _capacity(due_at=cs_due_moved(), calendar_blocks=[block])
    assert result.available_hours is not None
    assert abs(result.available_hours - 2.0) < 1e-6
    assert result.calendar_conflict is True


def test_buffer_applies_only_when_block_splits() -> None:
    rules = [
        AvailabilityRule(
            id="mon",
            weekday=0,
            starts_local="14:00",
            ends_local="18:00",
            timezone="America/New_York",
        )
    ]
    due = datetime(2026, 9, 8, 9, 0, tzinfo=V1_TZ)
    split = CalendarBlock(
        id="hole",
        source="gcal",
        external_id="hole",
        title="office hours",
        starts_at=datetime(2026, 9, 7, 15, 0, tzinfo=V1_TZ),
        ends_at=datetime(2026, 9, 7, 16, 0, tzinfo=V1_TZ),
    )
    result = _capacity(due_at=due, rules=rules, calendar_blocks=[split])
    assert result.available_hours is not None
    assert abs(result.available_hours - 2.5) < 1e-6
    assert abs(result.contiguous_minutes - 105.0) < 1e-6
    assert result.calendar_conflict is True
    assert len(result.intervals) == 2
    assert result.intervals[0] == (
        datetime(2026, 9, 7, 14, 0, tzinfo=V1_TZ),
        datetime(2026, 9, 7, 14, 45, tzinfo=V1_TZ),
    )
    assert result.intervals[1] == (
        datetime(2026, 9, 7, 16, 15, tzinfo=V1_TZ),
        datetime(2026, 9, 7, 18, 0, tzinfo=V1_TZ),
    )


def test_edge_clip_does_not_apply_buffer() -> None:
    result = _capacity(
        due_at=cs_due_moved(),
        calendar_blocks=[conflict_block_leaving_two_hours()],
    )
    assert result.available_hours is not None
    assert abs(result.available_hours - 2.0) < 1e-6


def test_normalize_due_at_midnight_becomes_end_of_day() -> None:
    midnight = datetime(2026, 9, 7, 0, 0, tzinfo=V1_TZ)
    normalized = normalize_due_at(midnight, tz=V1_TZ)
    assert normalized == datetime(
        2026,
        9,
        7,
        V1_THRESHOLDS.end_of_day_hour,
        V1_THRESHOLDS.end_of_day_minute,
        tzinfo=V1_TZ,
    )
    dated = normalize_due_at(date(2026, 9, 7), tz=V1_TZ)
    assert dated == normalized
    kept = normalize_due_at(cs_due_original(), tz=V1_TZ)
    assert kept == cs_due_original()
    assert normalize_due_at(None, tz=V1_TZ) is None


def test_has_availability_input_enabled_rule_or_available_override() -> None:
    rules = twelve_hour_rules()
    assert has_availability_input(rules, []) is True
    disabled = [{**rule, "enabled": False} for rule in rules]
    assert has_availability_input(disabled, []) is False
    available = AvailabilityOverride(
        id="sat",
        starts_at=datetime(2026, 9, 12, 14, 0, tzinfo=V1_TZ),
        ends_at=datetime(2026, 9, 12, 18, 0, tzinfo=V1_TZ),
        kind="available",
    )
    assert has_availability_input(disabled, [available]) is True


def test_available_override_without_rules_is_known_capacity() -> None:
    override = AvailabilityOverride(
        id="sat",
        starts_at=datetime(2026, 9, 12, 14, 0, tzinfo=V1_TZ),
        ends_at=datetime(2026, 9, 12, 18, 0, tzinfo=V1_TZ),
        kind="available",
    )
    result = _capacity(rules=[], overrides=[override], due_at=cs_due_original())
    assert result.reason is None
    assert result.available_hours is not None
    assert abs(result.available_hours - 4.0) < 1e-6


def test_shared_capacity_preparation_matches_individual_deadlines():
    from datetime import timedelta
    from nexus.core.availability import compute_capacities

    due_dates = [None, V1_NOW - timedelta(hours=1)] + [
        V1_NOW + timedelta(minutes=i * 37) for i in range(60)
    ]
    rules = [
        AvailabilityRule(str(i), i, "09:00", "22:00", "America/New_York")
        for i in range(7)
    ]
    blocks = [
        CalendarBlock(
            str(i),
            "test",
            str(i),
            "Busy",
            V1_NOW + timedelta(minutes=i * 50),
            V1_NOW + timedelta(minutes=i * 50 + 25),
        )
        for i in range(100)
    ]
    overrides = [
        AvailabilityOverride(
            "away",
            V1_NOW + timedelta(hours=1),
            V1_NOW + timedelta(hours=2),
            "unavailable",
        )
    ]
    for active_rules in (rules, []):
        inputs = dict(
            now=V1_NOW, rules=active_rules, overrides=overrides, calendar_blocks=blocks
        )
        batch = compute_capacities(due_dates=due_dates, **inputs)
        for due in due_dates:
            assert batch[due] == compute_capacity(due_at=due, **inputs)
