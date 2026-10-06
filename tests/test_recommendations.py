from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nexus.core.recommendations import rank_assessments, select_today
from nexus.core.utils import stable_id
from nexus.core.v1_config import V1_NOW, V1_THRESHOLDS

try:
    from nexus.core.risk import RiskAssessment
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class RiskAssessment:
        obligation_id: str
        band: str
        remaining_hours: float | None = None
        remaining_basis: str | None = None
        available_hours: float | None = None
        availability_reason: str | None = None
        load_ratio: float | None = None
        overload_gap: float | None = None
        reasons: tuple[str, ...] = ()
        computed_at: datetime = V1_NOW
        state_revision: int = 0
        due_at: datetime | None = None


def _assessment(**overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "obligation_id": "ob:default",
        "band": "low",
        "remaining_hours": 2.0,
        "remaining_basis": "manual",
        "available_hours": 4.0,
        "availability_reason": None,
        "load_ratio": 0.5,
        "overload_gap": 0.0,
        "reasons": ("within_horizon",),
        "computed_at": V1_NOW,
        "state_revision": 1,
        "due_at": V1_NOW + timedelta(days=5),
    }
    payload.update(overrides)
    try:
        return RiskAssessment(**payload)
    except TypeError:
        from types import SimpleNamespace

        return SimpleNamespace(**payload)


def test_select_today_is_deterministic() -> None:
    high = _assessment(
        obligation_id="ob:cs",
        band="high",
        remaining_hours=6.0,
        available_hours=2.0,
        load_ratio=3.0,
        overload_gap=4.0,
        reasons=("remaining_exceeds_available",),
        due_at=V1_NOW + timedelta(hours=24),
        state_revision=3,
    )
    medium = _assessment(
        obligation_id="ob:hist",
        band="medium",
        remaining_hours=3.0,
        available_hours=6.0,
        load_ratio=0.5,
        overload_gap=0.0,
        reasons=("deadline_within_72h",),
        due_at=V1_NOW + timedelta(hours=48),
        state_revision=3,
    )
    titles = {"ob:cs": "CS Assignment 2", "ob:hist": "History Reading"}
    first = select_today([medium, high], titles=titles, state_revision=3)
    second = select_today([medium, high], titles=titles, state_revision=3)
    assert first is not None and second is not None
    assert first.to_dict() == second.to_dict()
    assert first.recommendation_key == second.recommendation_key
    assert first.recommendation_key == "rk:" + stable_id(
        f"3|{first.obligation_id}|{first.action}|{first.block_minutes}"
    )
    assert "6h remaining vs 2h available" in first.explanation
    assert "remaining_exceeds_available" in first.explanation


def test_rank_high_above_medium_low_unscored() -> None:
    high = _assessment(
        obligation_id="ob:h",
        band="high",
        remaining_hours=6.0,
        available_hours=2.0,
        overload_gap=4.0,
        reasons=("remaining_exceeds_available",),
    )
    medium = _assessment(obligation_id="ob:m", band="medium")
    low = _assessment(obligation_id="ob:l", band="low")
    unscored = _assessment(
        obligation_id="ob:u",
        band="unscored",
        available_hours=None,
        availability_reason="availability_unknown",
        load_ratio=None,
        overload_gap=None,
        reasons=("availability_unknown",),
    )
    ranked = rank_assessments([unscored, low, medium, high])
    assert [item.obligation_id for item in ranked] == ["ob:h", "ob:m", "ob:l", "ob:u"]


def test_rank_tie_breaks() -> None:
    later = V1_NOW + timedelta(days=2)
    earlier = V1_NOW + timedelta(days=1)
    larger_gap = _assessment(
        obligation_id="ob:b",
        band="high",
        overload_gap=3.0,
        due_at=later,
        remaining_hours=1.0,
    )
    earlier_due = _assessment(
        obligation_id="ob:c",
        band="high",
        overload_gap=1.0,
        due_at=earlier,
        remaining_hours=1.0,
    )
    more_remaining = _assessment(
        obligation_id="ob:d",
        band="high",
        overload_gap=1.0,
        due_at=later,
        remaining_hours=5.0,
    )
    id_a = _assessment(
        obligation_id="ob:a",
        band="high",
        overload_gap=1.0,
        due_at=later,
        remaining_hours=1.0,
    )
    id_z = _assessment(
        obligation_id="ob:z",
        band="high",
        overload_gap=1.0,
        due_at=later,
        remaining_hours=1.0,
    )
    ranked = rank_assessments([id_z, more_remaining, earlier_due, id_a, larger_gap])
    assert [item.obligation_id for item in ranked] == [
        "ob:b",
        "ob:c",
        "ob:d",
        "ob:a",
        "ob:z",
    ]


def test_module_does_not_mention_priority() -> None:
    text = Path("src/nexus/core/recommendations.py").read_text(encoding="utf-8")
    assert "priority" not in text


def test_running_action_wins_over_overdue() -> None:
    overdue = _assessment(
        obligation_id="ob:overdue",
        band="overdue",
        remaining_hours=4.0,
        available_hours=1.0,
        overload_gap=3.0,
        reasons=("overdue",),
        due_at=V1_NOW - timedelta(hours=2),
    )
    running = _assessment(
        obligation_id="ob:running",
        band="low",
        remaining_hours=3.0,
        available_hours=8.0,
        overload_gap=0.0,
        reasons=("within_horizon",),
        due_at=V1_NOW + timedelta(days=6),
    )
    rec = select_today(
        [overdue, running],
        titles={"ob:overdue": "Late Lab", "ob:running": "Essay draft"},
        running_obligation_id="ob:running",
        running_elapsed_minutes=20,
        state_revision=2,
    )
    assert rec is not None
    assert rec.obligation_id == "ob:running"
    assert rec.action == "continue"
    assert rec.title == "Essay draft"


def test_missing_availability_is_setup() -> None:
    item = _assessment(
        obligation_id="ob:cs",
        band="unscored",
        remaining_hours=6.0,
        available_hours=None,
        availability_reason="availability_unknown",
        load_ratio=None,
        overload_gap=None,
        reasons=("availability_unknown",),
        due_at=V1_NOW + timedelta(days=1),
    )
    rec = select_today([item], titles={"ob:cs": "CS Assignment 2"})
    assert rec is not None
    assert rec.action == "setup"
    assert rec.risk_band == "unscored"
    assert rec.obligation_id == "ob:cs"
    assert rec.title == "CS Assignment 2"
    assert rec.missing_input is not None
    lowered = rec.missing_input.lower()
    assert "study" in lowered or "availability" in lowered


def test_empty_assessments_setup_missing_input() -> None:
    rec = select_today([], titles={"ob:cs": "CS Assignment 2"})
    assert rec is not None
    assert rec.action == "setup"
    assert rec.missing_input
    assert rec.obligation_id == ""
    blob = f"{rec.first_step} {rec.missing_input} {rec.title}".lower()
    assert "study course" not in blob
    assert "cs assignment" not in blob
    assert rec.first_step == (
        "Add a course, assignment, due date, remaining time, and when you can study."
    )


def test_first_step_default_and_manual_next_step() -> None:
    item = _assessment(obligation_id="ob:cs", band="medium", remaining_hours=2.0)
    rec = select_today([item], titles={"ob:cs": "CS Assignment 2"})
    assert rec is not None
    assert rec.first_step == (
        f"Open CS Assignment 2 and work for {rec.block_minutes} minutes"
    )
    rec2 = select_today(
        [item],
        titles={"ob:cs": "CS Assignment 2"},
        next_steps={"ob:cs": "Outline the proof of lemma 2."},
    )
    assert rec2 is not None
    assert rec2.first_step == "Outline the proof of lemma 2."


def test_block_minutes_capped_at_50() -> None:
    item = _assessment(obligation_id="ob:cs", band="medium", remaining_hours=6.0)
    rec = select_today(
        [item],
        titles={"ob:cs": "CS Assignment 2"},
        contiguous_minutes={"ob:cs": 180},
    )
    assert rec is not None
    assert rec.block_minutes == V1_THRESHOLDS.max_block_minutes
    assert rec.block_minutes == 50


def test_review_change_when_listed_and_not_running() -> None:
    item = _assessment(obligation_id="ob:cs", band="high", remaining_hours=6.0)
    rec = select_today(
        [item],
        titles={"ob:cs": "CS Assignment 2"},
        review_obligation_ids=["ob:cs"],
    )
    assert rec is not None
    assert rec.action == "review_change"

    rec_running = select_today(
        [item],
        titles={"ob:cs": "CS Assignment 2"},
        running_obligation_id="ob:cs",
        review_obligation_ids=["ob:cs"],
    )
    assert rec_running is not None
    assert rec_running.action == "continue"
    assert rec_running.obligation_id == "ob:cs"
