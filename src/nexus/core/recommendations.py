"""Pure Today ranking and Recommendation model. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .utils import stable_id
from .v1_config import RISK_SEVERITY, V1_THRESHOLDS

try:
    from .risk import RiskAssessment
except ImportError:  # sibling module may land later; ranking is duck-typed
    RiskAssessment = Any  # type: ignore[misc,assignment]

_SETUP_EMPTY_STEP = (
    "Add a course, assignment, due date, remaining time, and when you can study."
)
_SETUP_EMPTY_MISSING = (
    "Nothing is open. Add a course, assignment, due date, remaining time, "
    "and when you can study."
)
_SETUP_AVAILABILITY = "Add when you can study this week to calculate risk"


@dataclass(frozen=True)
class Recommendation:
    obligation_id: str
    title: str
    action: str  # start | continue | finish | review_change | setup
    block_minutes: int
    first_step: str
    risk_band: str
    reasons: tuple[str, ...]
    remaining_hours: float | None
    available_hours: float | None
    recommendation_key: str
    missing_input: str | None = None
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "title": self.title,
            "action": self.action,
            "block_minutes": self.block_minutes,
            "first_step": self.first_step,
            "risk_band": self.risk_band,
            "reasons": list(self.reasons),
            "remaining_hours": self.remaining_hours,
            "available_hours": self.available_hours,
            "recommendation_key": self.recommendation_key,
            "missing_input": self.missing_input,
            "explanation": self.explanation,
        }


def rank_assessments(
    assessments: Sequence[RiskAssessment],
    *,
    due_at_by_id: Mapping[str, datetime | None] | None = None,
) -> list[RiskAssessment]:
    """Sort by severity, positive overload gap, deadline, remaining, id."""

    def due_of(item: RiskAssessment) -> datetime | None:
        oid = str(getattr(item, "obligation_id", "") or "")
        if due_at_by_id is not None and oid in due_at_by_id:
            return due_at_by_id[oid]
        return getattr(item, "due_at", None)

    def sort_key(item: RiskAssessment) -> tuple:
        band = getattr(item, "band", None) or "unscored"
        severity = RISK_SEVERITY.get(band, len(RISK_SEVERITY))
        gap = getattr(item, "overload_gap", None)
        positive_gap = float(gap) if gap is not None and gap > 0 else 0.0
        due = due_of(item)
        remaining = getattr(item, "remaining_hours", None)
        remaining_value = 0.0 if remaining is None else float(remaining)
        oid = str(getattr(item, "obligation_id", "") or "")
        return (
            severity,
            -positive_gap,
            due is None,
            _due_timestamp(due),
            -remaining_value,
            oid,
        )

    return sorted(assessments, key=sort_key)


def build_recommendation(
    *,
    top: RiskAssessment | None,
    title: str,
    running_obligation_id: str | None = None,
    running_title: str | None = None,
    running_elapsed_minutes: float | None = None,
    next_step: str | None = None,
    contiguous_minutes: float | None = None,
    state_revision: int = 0,
    remaining_minutes: float | None = None,
    needs_review_change: bool = False,
) -> Recommendation | None:
    if running_obligation_id:
        source = top if _same_obligation(top, running_obligation_id) else None
        rec_title = running_title or title
        action = _work_action(
            running=True,
            remaining_minutes=remaining_minutes,
            needs_review_change=False,
        )
        return _from_source(
            source=source,
            obligation_id=running_obligation_id,
            title=rec_title,
            action=action,
            next_step=next_step,
            contiguous_minutes=contiguous_minutes,
            remaining_minutes=remaining_minutes,
            state_revision=state_revision,
            running_elapsed_minutes=running_elapsed_minutes,
        )

    if top is None:
        return _setup_empty(state_revision=state_revision)

    if (
        getattr(top, "band", None) == "unscored"
        and getattr(top, "availability_reason", None) == "availability_unknown"
    ):
        return _setup_availability(
            top=top,
            title=title,
            next_step=next_step,
            contiguous_minutes=contiguous_minutes,
            remaining_minutes=remaining_minutes,
            state_revision=state_revision,
        )

    action = _work_action(
        running=False,
        remaining_minutes=remaining_minutes,
        needs_review_change=needs_review_change,
    )
    return _from_source(
        source=top,
        obligation_id=str(top.obligation_id),
        title=title,
        action=action,
        next_step=next_step,
        contiguous_minutes=contiguous_minutes,
        remaining_minutes=remaining_minutes,
        state_revision=state_revision,
        running_elapsed_minutes=None,
    )


def select_today(
    assessments: Sequence[RiskAssessment],
    *,
    titles: Mapping[str, str],
    running_obligation_id: str | None = None,
    running_elapsed_minutes: float | None = None,
    next_steps: Mapping[str, str] | None = None,
    contiguous_minutes: Mapping[str, float] | None = None,
    state_revision: int = 0,
    review_obligation_ids: Sequence[str] | None = None,
) -> Recommendation | None:
    """Rank then build. Running action wins. Same inputs → identical to_dict()."""
    if running_obligation_id:
        source = _find(assessments, running_obligation_id)
        return build_recommendation(
            top=source,
            title=titles.get(running_obligation_id, ""),
            running_obligation_id=running_obligation_id,
            running_title=titles.get(running_obligation_id, ""),
            running_elapsed_minutes=running_elapsed_minutes,
            next_step=_mapped(next_steps, running_obligation_id),
            contiguous_minutes=_mapped(contiguous_minutes, running_obligation_id),
            state_revision=state_revision,
            remaining_minutes=_remaining_minutes(source),
            needs_review_change=False,
        )

    ranked = rank_assessments(assessments)
    top = ranked[0] if ranked else None
    if top is None:
        return build_recommendation(top=None, title="", state_revision=state_revision)

    oid = str(top.obligation_id)
    review_ids = set(review_obligation_ids or ())
    return build_recommendation(
        top=top,
        title=titles.get(oid, ""),
        next_step=_mapped(next_steps, oid),
        contiguous_minutes=_mapped(contiguous_minutes, oid),
        state_revision=state_revision,
        remaining_minutes=_remaining_minutes(top),
        needs_review_change=oid in review_ids,
    )


def _due_timestamp(due: datetime | None) -> float:
    if due is None:
        return 0.0
    return due.timestamp()


def _same_obligation(top: RiskAssessment | None, obligation_id: str) -> bool:
    return top is not None and str(getattr(top, "obligation_id", "")) == obligation_id


def _find(assessments: Sequence[RiskAssessment], obligation_id: str) -> RiskAssessment | None:
    for item in assessments:
        if str(getattr(item, "obligation_id", "")) == obligation_id:
            return item
    return None


def _mapped(values: Mapping[str, Any] | None, key: str) -> Any | None:
    if values is None:
        return None
    return values.get(key)


def _remaining_minutes(assessment: RiskAssessment | None) -> float | None:
    if assessment is None:
        return None
    hours = getattr(assessment, "remaining_hours", None)
    if hours is None:
        return None
    return float(hours) * 60.0


def _reasons_of(source: RiskAssessment | None) -> tuple[str, ...]:
    if source is None:
        return ()
    raw = getattr(source, "reasons", ()) or ()
    return tuple(str(item) for item in raw)


def _band_of(source: RiskAssessment | None) -> str:
    if source is None:
        return "unscored"
    return str(getattr(source, "band", None) or "unscored")


def _hours_of(source: RiskAssessment | None, field: str) -> float | None:
    if source is None:
        return None
    value = getattr(source, field, None)
    if value is None:
        return None
    return float(value)


def _block_minutes(
    remaining_minutes: float | None,
    contiguous_minutes: float | None,
) -> int:
    max_block = int(V1_THRESHOLDS.max_block_minutes)
    rem = max_block if remaining_minutes is None else float(remaining_minutes)
    contig = max_block if contiguous_minutes is None else float(contiguous_minutes)
    block = min(rem, contig, float(max_block))
    if remaining_minutes is not None and remaining_minutes <= 0:
        return 0
    if block <= 0:
        if remaining_minutes is not None and remaining_minutes > 0:
            return 1
        return 0
    minutes = int(block)
    if remaining_minutes is not None and remaining_minutes > 0 and minutes < 1:
        return 1
    return minutes


def _work_action(
    *,
    running: bool,
    remaining_minutes: float | None,
    needs_review_change: bool,
) -> str:
    finish_within = float(V1_THRESHOLDS.transition_buffer_minutes)
    if running:
        if remaining_minutes is not None and remaining_minutes <= finish_within:
            return "finish"
        return "continue"
    if needs_review_change:
        return "review_change"
    if remaining_minutes is not None and remaining_minutes <= finish_within:
        return "finish"
    return "start"


def _recommendation_key(
    state_revision: int,
    obligation_id: str,
    action: str,
    block_minutes: int,
) -> str:
    seed = f"{state_revision}|{obligation_id}|{action}|{block_minutes}"
    return "rk:" + stable_id(seed)


def _fmt_hours(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _default_first_step(title: str, block_minutes: int) -> str:
    return f"Open {title} and work for {block_minutes} minutes"


def _explanation(
    *,
    reasons: tuple[str, ...],
    remaining_hours: float | None,
    available_hours: float | None,
    running_elapsed_minutes: float | None,
    missing_input: str | None = None,
) -> str:
    parts: list[str] = []
    if remaining_hours is not None and available_hours is not None:
        parts.append(
            f"{_fmt_hours(remaining_hours)}h remaining vs "
            f"{_fmt_hours(available_hours)}h available"
        )
    elif remaining_hours is not None:
        parts.append(f"{_fmt_hours(remaining_hours)}h remaining")
    elif available_hours is not None:
        parts.append(f"{_fmt_hours(available_hours)}h available")
    if reasons:
        parts.append(reasons[0])
    if running_elapsed_minutes is not None:
        elapsed = int(running_elapsed_minutes)
        parts.append(f"already in progress for {elapsed} minutes")
    if missing_input and missing_input not in parts:
        parts.append(missing_input)
    return ". ".join(parts)


def _setup_empty(*, state_revision: int) -> Recommendation:
    action = "setup"
    block = 0
    return Recommendation(
        obligation_id="",
        title="",
        action=action,
        block_minutes=block,
        first_step=_SETUP_EMPTY_STEP,
        risk_band="unscored",
        reasons=(),
        remaining_hours=None,
        available_hours=None,
        recommendation_key=_recommendation_key(state_revision, "", action, block),
        missing_input=_SETUP_EMPTY_MISSING,
        explanation=_SETUP_EMPTY_MISSING,
    )


def _setup_availability(
    *,
    top: RiskAssessment,
    title: str,
    next_step: str | None,
    contiguous_minutes: float | None,
    remaining_minutes: float | None,
    state_revision: int,
) -> Recommendation:
    action = "setup"
    block = _block_minutes(remaining_minutes, contiguous_minutes)
    reasons = _reasons_of(top)
    remaining_hours = _hours_of(top, "remaining_hours")
    available_hours = _hours_of(top, "available_hours")
    first_step = next_step or _SETUP_AVAILABILITY
    return Recommendation(
        obligation_id=str(top.obligation_id),
        title=title,
        action=action,
        block_minutes=block,
        first_step=first_step,
        risk_band="unscored",
        reasons=reasons,
        remaining_hours=remaining_hours,
        available_hours=available_hours,
        recommendation_key=_recommendation_key(
            state_revision, str(top.obligation_id), action, block
        ),
        missing_input=_SETUP_AVAILABILITY,
        explanation=_explanation(
            reasons=reasons,
            remaining_hours=remaining_hours,
            available_hours=available_hours,
            running_elapsed_minutes=None,
            missing_input=_SETUP_AVAILABILITY,
        ),
    )


def _from_source(
    *,
    source: RiskAssessment | None,
    obligation_id: str,
    title: str,
    action: str,
    next_step: str | None,
    contiguous_minutes: float | None,
    remaining_minutes: float | None,
    state_revision: int,
    running_elapsed_minutes: float | None,
) -> Recommendation:
    block = _block_minutes(remaining_minutes, contiguous_minutes)
    reasons = _reasons_of(source)
    remaining_hours = _hours_of(source, "remaining_hours")
    available_hours = _hours_of(source, "available_hours")
    first_step = next_step or _default_first_step(title, block)
    return Recommendation(
        obligation_id=obligation_id,
        title=title,
        action=action,
        block_minutes=block,
        first_step=first_step,
        risk_band=_band_of(source),
        reasons=reasons,
        remaining_hours=remaining_hours,
        available_hours=available_hours,
        recommendation_key=_recommendation_key(
            state_revision, obligation_id, action, block
        ),
        missing_input=None,
        explanation=_explanation(
            reasons=reasons,
            remaining_hours=remaining_hours,
            available_hours=available_hours,
            running_elapsed_minutes=running_elapsed_minutes,
        ),
    )
