from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nexus.learning_focus import build_learning_focus
from nexus.models import Task


def test_focus_links_weak_concept_to_nearest_open_course_task() -> None:
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    tasks = [
        Task("later", "Final exam", now + timedelta(days=10), "gcal", course="OS"),
        Task("soon", "Deadlock lab", now + timedelta(days=2), "brightspace", course="OS"),
        Task("other", "Calculus quiz", now + timedelta(days=1), "gcal", course="MATH"),
    ]
    learning = {
        "available": True,
        "evidence": [{
            "concept_id": "os__deadlock",
            "concept_name": "Deadlock",
            "course_id": "OS",
            "current_mastery": 0.18,
            "urgency": "critical",
            "source": "offline_quiz",
            "trust_status": "trusted",
        }],
        "review_queue": [],
        "weak_points": [],
    }

    focus = build_learning_focus(tasks, learning)

    assert focus[0]["name"] == "Deadlock"
    assert focus[0]["related_task"]["id"] == "soon"
    assert focus[0]["evidence_source"] == "offline_quiz"
    assert focus[0]["trust_status"] == "trusted"


def test_focus_deduplicates_evidence_and_accepts_user_reported_weak_points() -> None:
    learning = {
        "available": True,
        "evidence": [
            {"concept_id": "os__lock", "concept_name": "Lock", "current_mastery": 0.2},
            {"concept_id": "os__lock", "concept_name": "Lock", "current_mastery": 0.2},
        ],
        "weak_points": [
            {"concept": "Lock", "course": "OS"},
            {"concept": "Scheduling", "course": "OS"},
        ],
    }

    focus = build_learning_focus([], learning)

    assert [item["name"] for item in focus] == ["Lock", "Scheduling"]
    assert focus[1]["trust_status"] == "user_reported"


def test_focus_prefers_first_recent_trusted_provenance() -> None:
    learning = {
        "available": True,
        "evidence": [
            {
                "concept_id": "os__lock",
                "concept_name": "Lock",
                "source": "recent_quiz",
                "trust_status": "trusted",
                "timestamp": "2026-08-07T10:00:00Z",
            },
            {
                "concept_id": "os__lock",
                "concept_name": "Lock",
                "source": "old_quiz",
                "trust_status": "trusted",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        ],
    }

    focus = build_learning_focus([], learning)

    assert focus[0]["evidence_source"] == "recent_quiz"
    assert focus[0]["evidence_at"] == "2026-08-07T10:00:00Z"
