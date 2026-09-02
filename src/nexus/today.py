"""Read-only Today view: state + capacity + risk + recommendation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .academic_state import _as_float, _parse_due
from .academic_state_service import AcademicStateService, _resolve_now
from .action_service import ActionService
from .availability import (
    AvailabilityOverride,
    AvailabilityRule,
    CalendarBlock,
    CapacityResult,
    compute_capacity,
)
from .intelligence.risk import RiskAssessment, estimate_remaining, evaluate_obligation_risk
from .intelligence.workload import (
    DEFAULT_EFFORT_HOURS,
    EFFORT_HOURS_BY_TAG,
    learned_effort_hours,
)
from .recommendations import Recommendation, select_today
from .v1_config import ACTIVE_OBLIGATION_STATUSES, V1_THRESHOLDS


@dataclass
class TodayView:
    now: datetime
    revision: int
    recommendation: Recommendation | None
    assessments: list[RiskAssessment] = field(default_factory=list)
    running: dict[str, Any] | None = None
    missing_input: str | None = None
    freshness: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "now": self.now.isoformat(),
            "revision": self.revision,
            "recommendation": None
            if self.recommendation is None
            else self.recommendation.to_dict(),
            "assessments": [
                {
                    "obligation_id": item.obligation_id,
                    "band": item.band,
                    "remaining_hours": item.remaining_hours,
                    "available_hours": item.available_hours,
                    "load_ratio": item.load_ratio,
                    "overload_gap": item.overload_gap,
                    "reasons": list(item.reasons),
                }
                for item in self.assessments
            ],
            "running": self.running,
            "missing_input": self.missing_input,
            "freshness": self.freshness,
        }


def _truthy_flag(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip() not in {"", "0", "false", "False"}
    return bool(value)


def _rules_from_rows(rows: list[dict[str, Any]]) -> list[AvailabilityRule]:
    rules: list[AvailabilityRule] = []
    for row in rows:
        rules.append(
            AvailabilityRule(
                id=str(row.get("id") or ""),
                weekday=int(row["weekday"]),
                starts_local=str(row["starts_local"]),
                ends_local=str(row["ends_local"]),
                timezone=str(row.get("timezone") or "America/New_York"),
                enabled=_truthy_flag(row.get("enabled"), True),
            )
        )
    return rules


def _overrides_from_rows(rows: list[dict[str, Any]]) -> list[AvailabilityOverride]:
    items: list[AvailabilityOverride] = []
    for row in rows:
        starts = _parse_due(row.get("starts_at"))
        ends = _parse_due(row.get("ends_at"))
        if starts is None or ends is None:
            continue
        items.append(
            AvailabilityOverride(
                id=str(row.get("id") or ""),
                starts_at=starts,
                ends_at=ends,
                kind=str(row.get("kind") or "unavailable"),
                note=row.get("note"),
            )
        )
    return items


def _blocks_from_rows(rows: list[dict[str, Any]]) -> list[CalendarBlock]:
    items: list[CalendarBlock] = []
    for row in rows:
        starts = _parse_due(row.get("starts_at"))
        ends = _parse_due(row.get("ends_at"))
        if starts is None or ends is None:
            continue
        items.append(
            CalendarBlock(
                id=str(row.get("id") or ""),
                source=str(row.get("source") or ""),
                external_id=str(row.get("external_id") or ""),
                title=str(row.get("title") or ""),
                starts_at=starts,
                ends_at=ends,
                course_id=row.get("course_id"),
                kind=str(row.get("kind") or "busy"),
                status=str(row.get("status") or "confirmed"),
            )
        )
    return items


def _history_map(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for sample in samples:
        sid = str(sample.get("id") or f"sample-{len(mapped)}")
        mapped[sid] = {
            "effort_key": sample.get("kind") or "default",
            "effort_minutes": sample.get("minutes"),
        }
    return mapped


def evaluate_state(
    state: dict[str, Any],
    *,
    now: datetime,
) -> tuple[list[RiskAssessment], dict[str, float], dict[str, str], dict[str, str]]:
    rules = _rules_from_rows(state.get("availability_rules") or [])
    overrides = _overrides_from_rows(state.get("availability_overrides") or [])
    blocks = _blocks_from_rows(state.get("calendar_blocks") or [])
    history = _history_map(state.get("effort_samples") or [])
    revision = int(state.get("revision") or 0)
    assessments: list[RiskAssessment] = []
    contiguous: dict[str, float] = {}
    titles: dict[str, str] = {}
    next_steps: dict[str, str] = {}
    for row in state.get("obligations") or []:
        oid = str(row.get("id") or "")
        status = str(row.get("status") or "open")
        titles[oid] = str(row.get("title") or "")
        if row.get("next_step"):
            next_steps[oid] = str(row["next_step"])
        if status not in ACTIVE_OBLIGATION_STATUSES:
            continue
        kind = str(row.get("kind") or "default")
        spent = _as_float(row.get("spent_hours")) or 0.0
        effort = estimate_remaining(
            estimated_hours=_as_float(row.get("estimated_hours")),
            spent_hours=spent,
            remaining_override_hours=_as_float(row.get("remaining_override_hours")),
            history_hours=learned_effort_hours(history, kind),
            type_prior_hours=EFFORT_HOURS_BY_TAG.get(kind, DEFAULT_EFFORT_HOURS),
            status=status,
        )
        due = _parse_due(row.get("due_at"))
        capacity = compute_capacity(
            now=now,
            due_at=due,
            rules=rules,
            overrides=overrides,
            calendar_blocks=blocks,
            tz=now.tzinfo,
            thresholds=V1_THRESHOLDS,
        )
        contiguous[oid] = float(capacity.contiguous_minutes or 0.0)
        assessments.append(
            evaluate_obligation_risk(
                obligation_id=oid,
                due_at=due,
                status=status,
                remaining_hours=effort.remaining_hours,
                remaining_basis=effort.basis,
                capacity=capacity,
                now=now,
                state_revision=revision,
            )
        )
    return assessments, contiguous, titles, next_steps


def build_today(data_dir: Path, *, now: datetime | None = None) -> TodayView:
    current = _resolve_now(now)
    service = AcademicStateService(data_dir)
    actions = ActionService(data_dir)
    state = service.load_state()
    samples = service.store.list_effort_samples()
    state["effort_samples"] = samples
    assessments, contiguous, titles, next_steps = evaluate_state(state, now=current)
    running = actions.running_action(now=current)
    running_id = None if running is None else str(running["obligation_id"])
    recommendation = select_today(
        assessments,
        titles=titles,
        running_obligation_id=running_id,
        running_elapsed_minutes=None if running is None else running.get("elapsed_minutes"),
        next_steps=next_steps,
        contiguous_minutes=contiguous,
        state_revision=int(state.get("revision") or 0),
    )
    missing = None if recommendation is None else recommendation.missing_input
    return TodayView(
        now=current,
        revision=int(state.get("revision") or 0),
        recommendation=recommendation,
        assessments=assessments,
        running=running,
        missing_input=missing,
        freshness=current.isoformat(),
    )


def empty_capacity() -> CapacityResult:
    return CapacityResult(
        available_hours=None,
        reason="availability_unknown",
        contiguous_minutes=0.0,
        intervals=(),
        calendar_conflict=False,
    )
