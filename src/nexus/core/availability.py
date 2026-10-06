"""Pure available-time capacity. No I/O, no clock, no LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Iterable, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

from .v1_config import V1_THRESHOLDS, V1_TZ, V1_TIMEZONE_NAME, RiskThresholds

Interval = tuple[datetime, datetime]

_SKIP_BLOCK_STATUSES = frozenset({"cancelled", "canceled", "skipped"})
_FREE_BLOCK_KINDS = frozenset({"free", "available", "transparent"})


@dataclass(frozen=True)
class AvailabilityRule:
    id: str
    weekday: int  # 0=Monday
    starts_local: str  # 'HH:MM'
    ends_local: str
    timezone: str
    enabled: bool = True


@dataclass(frozen=True)
class AvailabilityOverride:
    id: str
    starts_at: datetime
    ends_at: datetime
    kind: str  # 'available' | 'unavailable'
    note: str | None = None


@dataclass(frozen=True)
class CalendarBlock:
    id: str
    source: str
    external_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
    course_id: str | None = None
    kind: str = "busy"
    status: str = "confirmed"


@dataclass(frozen=True)
class CapacityResult:
    available_hours: float | None
    reason: str | None  # None or 'availability_unknown'
    contiguous_minutes: float
    intervals: tuple[Interval, ...]
    calendar_conflict: bool = False


def _as_zone(value: tzinfo | str | None) -> ZoneInfo:
    if value is None:
        return V1_TZ
    if isinstance(value, ZoneInfo):
        return value
    if isinstance(value, str):
        return ZoneInfo(value)
    key = getattr(value, "key", None)
    if isinstance(key, str):
        return ZoneInfo(key)
    if value is timezone.utc or str(value) in {"UTC", "utc"}:
        return ZoneInfo("UTC")
    return V1_TZ


def _aware(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_s, minute_s = value.strip().split(":", 1)
    return int(hour_s), int(minute_s)


def _duration_hours(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 3600.0, 0.0)


def _duration_minutes(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 60.0, 0.0)


def _clip(interval: Interval, start: datetime, end: datetime) -> Interval | None:
    clipped_start = max(interval[0], start)
    clipped_end = min(interval[1], end)
    if clipped_end > clipped_start:
        return (clipped_start, clipped_end)
    return None


def _merge(intervals: Iterable[Interval]) -> list[Interval]:
    items = sorted((start, end) for start, end in intervals if end > start)
    if not items:
        return []
    merged: list[Interval] = [items[0]]
    for start, end in items[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract(
    available: Iterable[Interval], busy: Iterable[Interval]
) -> list[Interval]:
    result = _merge(available)
    for busy_start, busy_end in _merge(busy):
        next_result: list[Interval] = []
        for start, end in result:
            if busy_end <= start or busy_start >= end:
                next_result.append((start, end))
                continue
            if start < busy_start:
                next_result.append((start, busy_start))
            if busy_end < end:
                next_result.append((busy_end, end))
        result = next_result
    return [(start, end) for start, end in result if end > start]


def _iter_rule_windows(
    rule: AvailabilityRule,
    now: datetime,
    due: datetime,
    tz: ZoneInfo,
) -> Iterator[Interval]:
    if not rule.enabled:
        return
    rule_tz = ZoneInfo(rule.timezone) if rule.timezone else tz
    start_h, start_m = _parse_hhmm(rule.starts_local)
    end_h, end_m = _parse_hhmm(rule.ends_local)
    wraps = (start_h, start_m) > (end_h, end_m)
    now_local = now.astimezone(rule_tz)
    due_local = due.astimezone(rule_tz)
    day = (now_local - timedelta(days=1)).date()
    last = due_local.date()
    while day <= last:
        if day.weekday() == rule.weekday:
            start_dt = datetime(
                day.year, day.month, day.day, start_h, start_m, tzinfo=rule_tz
            )
            if wraps:
                end_day = day + timedelta(days=1)
                end_dt = datetime(
                    end_day.year,
                    end_day.month,
                    end_day.day,
                    end_h,
                    end_m,
                    tzinfo=rule_tz,
                )
            else:
                end_dt = datetime(
                    day.year, day.month, day.day, end_h, end_m, tzinfo=rule_tz
                )
            start_dt = start_dt.astimezone(tz)
            end_dt = end_dt.astimezone(tz)
            clipped = _clip((start_dt, end_dt), now, due)
            if clipped:
                yield clipped
        day += timedelta(days=1)


def _coerce_rule(value: AvailabilityRule | Mapping[str, Any]) -> AvailabilityRule:
    if isinstance(value, AvailabilityRule):
        return value
    weekday = int(value["weekday"])
    starts = str(value["starts_local"])
    ends = str(value["ends_local"])
    zone = str(value.get("timezone") or V1_TIMEZONE_NAME)
    ident = str(value.get("id") or f"rule-{weekday}-{starts}-{ends}")
    return AvailabilityRule(
        id=ident,
        weekday=weekday,
        starts_local=starts,
        ends_local=ends,
        timezone=zone,
        enabled=bool(value.get("enabled", True)),
    )


def _coerce_override(
    value: AvailabilityOverride | Mapping[str, Any]
) -> AvailabilityOverride:
    if isinstance(value, AvailabilityOverride):
        return value
    starts_at = value["starts_at"]
    ends_at = value["ends_at"]
    kind = str(value.get("kind") or "")
    ident = str(value.get("id") or f"override-{kind}-{starts_at}-{ends_at}")
    note = value.get("note")
    return AvailabilityOverride(
        id=ident,
        starts_at=starts_at,
        ends_at=ends_at,
        kind=kind,
        note=None if note is None else str(note),
    )


def _coerce_block(value: CalendarBlock | Mapping[str, Any]) -> CalendarBlock:
    if isinstance(value, CalendarBlock):
        return value
    starts_at = value["starts_at"]
    ends_at = value["ends_at"]
    source = str(value.get("source") or "")
    external_id = str(value.get("external_id") or "")
    ident = str(value.get("id") or external_id or f"block-{starts_at}-{ends_at}")
    course_id = value.get("course_id")
    return CalendarBlock(
        id=ident,
        source=source,
        external_id=external_id,
        title=str(value.get("title") or ""),
        starts_at=starts_at,
        ends_at=ends_at,
        course_id=None if course_id is None else str(course_id),
        kind=str(value.get("kind") or "busy"),
        status=str(value.get("status") or "confirmed"),
    )


def _is_occupied_block(block: CalendarBlock) -> bool:
    status = (block.status or "").strip().casefold()
    if status in _SKIP_BLOCK_STATUSES:
        return False
    kind = (block.kind or "busy").strip().casefold()
    if kind in _FREE_BLOCK_KINDS:
        return False
    return True


def _subtract_busy_with_buffer(
    available: Sequence[Interval],
    busy: Sequence[Interval],
    buffer: timedelta,
) -> list[Interval]:
    merged_busy = _merge(busy)
    result: list[Interval] = []
    for a_start, a_end in available:
        overlapping: list[Interval] = []
        for b_start, b_end in merged_busy:
            start = max(a_start, b_start)
            end = min(a_end, b_end)
            if end > start:
                overlapping.append((start, end))
        if not overlapping:
            result.append((a_start, a_end))
            continue
        pieces: list[Interval] = []
        cursor = a_start
        for start, end in overlapping:
            if start > cursor:
                pieces.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < a_end:
            pieces.append((cursor, a_end))
        splitters = [
            (start, end)
            for start, end in overlapping
            if start > a_start and end < a_end
        ]
        if not splitters:
            result.extend(pieces)
            continue
        for p_start, p_end in pieces:
            start, end = p_start, p_end
            for hole_start, hole_end in splitters:
                if p_end == hole_start:
                    end = min(end, p_end - buffer)
                if p_start == hole_end:
                    start = max(start, p_start + buffer)
            if end > start:
                result.append((start, end))
    return _merge(result)


def normalize_due_at(
    due_at: datetime | date | None, *, tz: tzinfo | str | None
) -> datetime | None:
    """Date-only / midnight due dates become local 23:59 that day."""
    if due_at is None:
        return None
    zone = _as_zone(tz)
    if not isinstance(due_at, datetime):
        return datetime(
            due_at.year,
            due_at.month,
            due_at.day,
            V1_THRESHOLDS.end_of_day_hour,
            V1_THRESHOLDS.end_of_day_minute,
            tzinfo=zone,
        )
    local = _aware(due_at, zone)
    if (
        local.hour == 0
        and local.minute == 0
        and local.second == 0
        and local.microsecond == 0
    ):
        return local.replace(
            hour=V1_THRESHOLDS.end_of_day_hour,
            minute=V1_THRESHOLDS.end_of_day_minute,
            second=0,
            microsecond=0,
        )
    return local


def has_availability_input(
    rules: Iterable[AvailabilityRule | Mapping[str, Any]] | None,
    overrides: Iterable[AvailabilityOverride | Mapping[str, Any]] | None,
) -> bool:
    for rule in rules or ():
        if _coerce_rule(rule).enabled:
            return True
    for override in overrides or ():
        if _coerce_override(override).kind == "available":
            return True
    return False


def compute_capacity(
    *,
    now: datetime,
    due_at: datetime | date | None,
    rules: Iterable[AvailabilityRule | Mapping[str, Any]] | None,
    overrides: Iterable[AvailabilityOverride | Mapping[str, Any]] | None,
    calendar_blocks: Iterable[CalendarBlock | Mapping[str, Any]] | None,
    tz: tzinfo | str | None = None,
    thresholds: RiskThresholds = V1_THRESHOLDS,
) -> CapacityResult:
    zone = _as_zone(tz or now.tzinfo)
    now_local = _aware(now, zone)
    due = normalize_due_at(due_at, tz=zone)
    if not has_availability_input(rules, overrides):
        return CapacityResult(
            available_hours=None,
            reason="availability_unknown",
            contiguous_minutes=0.0,
            intervals=(),
            calendar_conflict=False,
        )
    if due is None or due <= now_local:
        return CapacityResult(
            available_hours=0.0,
            reason=None,
            contiguous_minutes=0.0,
            intervals=(),
            calendar_conflict=False,
        )

    available, busy = _capacity_inputs(
        now_local, due, rules, overrides, calendar_blocks, zone
    )
    return _capacity_result(now_local, due, available, busy, thresholds)


def _capacity_inputs(now_local, due, rules, overrides, calendar_blocks, zone):
    available: list[Interval] = []
    for rule in rules or ():
        available.extend(_iter_rule_windows(_coerce_rule(rule), now_local, due, zone))

    unavailable: list[Interval] = []
    for raw in overrides or ():
        override = _coerce_override(raw)
        clipped = _clip(
            (_aware(override.starts_at, zone), _aware(override.ends_at, zone)),
            now_local,
            due,
        )
        if clipped is None:
            continue
        if override.kind == "available":
            available.append(clipped)
        elif override.kind == "unavailable":
            unavailable.append(clipped)

    available = _subtract(_merge(available), unavailable)

    busy: list[Interval] = []
    for raw in calendar_blocks or ():
        block = _coerce_block(raw)
        if not _is_occupied_block(block):
            continue
        start = _aware(block.starts_at, zone)
        end = _aware(block.ends_at, zone)
        if end <= start or (end - start) >= timedelta(hours=24):
            continue
        clipped = _clip((start, end), now_local, due)
        if clipped is not None:
            busy.append(clipped)
    busy = _merge(busy)

    return available, busy


def _capacity_result(now_local, due, available, busy, thresholds):
    calendar_conflict = False
    for b_start, b_end in busy:
        for a_start, a_end in available:
            if b_start < a_end and b_end > a_start:
                calendar_conflict = True
                break
        if calendar_conflict:
            break

    buffer = timedelta(minutes=thresholds.transition_buffer_minutes)
    remaining = _subtract_busy_with_buffer(available, busy, buffer)
    hours = sum(_duration_hours(start, end) for start, end in remaining)
    max_hours = (due - now_local).total_seconds() / 3600.0
    hours = min(max(hours, 0.0), max_hours)
    contiguous = max(
        (_duration_minutes(start, end) for start, end in remaining), default=0.0
    )
    return CapacityResult(
        available_hours=hours,
        reason=None,
        contiguous_minutes=contiguous,
        intervals=tuple(remaining),
        calendar_conflict=calendar_conflict,
    )


def compute_capacities(
    *,
    now: datetime,
    due_dates: Iterable[datetime | date | None],
    rules: Sequence[AvailabilityRule | Mapping[str, Any]],
    overrides: Sequence[AvailabilityOverride | Mapping[str, Any]],
    calendar_blocks: Sequence[CalendarBlock | Mapping[str, Any]],
    tz: tzinfo | str | None = None,
    thresholds: RiskThresholds = V1_THRESHOLDS,
) -> dict[datetime | date | None, CapacityResult]:
    """Prepare shared intervals once, then clip them to each exact deadline."""
    zone = _as_zone(tz or now.tzinfo)
    now_local = _aware(now, zone)
    dates = {raw: normalize_due_at(raw, tz=zone) for raw in due_dates}
    if not has_availability_input(rules, overrides):
        unknown = CapacityResult(None, "availability_unknown", 0.0, ())
        return dict.fromkeys(dates, unknown)
    future = [due for due in dates.values() if due is not None and due > now_local]
    if not future:
        return dict.fromkeys(dates, CapacityResult(0.0, None, 0.0, ()))
    available, busy = _capacity_inputs(
        now_local, max(future), rules, overrides, calendar_blocks, zone
    )
    results = {}
    for raw, due in dates.items():
        if due is None or due <= now_local:
            results[raw] = CapacityResult(0.0, None, 0.0, ())
            continue
        windows = [
            clipped
            for interval in available
            if (clipped := _clip(interval, now_local, due))
        ]
        occupied = [
            clipped for interval in busy if (clipped := _clip(interval, now_local, due))
        ]
        results[raw] = _capacity_result(now_local, due, windows, occupied, thresholds)
    return results
