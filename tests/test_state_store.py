from __future__ import annotations

from nexus.state_store import LearnerState, load_state, save_state


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = LearnerState(
        major="EE",
        current_focus="Analog Circuits",
        weak_points=["feedback"],
        review_queue=["op-amp"],
        mastery={"op-amp": 0.3},
        weak_point_confidence={"feedback": 0.8},
        weak_point_status={"feedback": "active"},
        weak_point_evidence={"feedback": ["confused with feedback sign"]},
    )
    save_state(path, state)

    loaded = load_state(path)
    assert loaded.major == "EE"
    assert loaded.mastery["op-amp"] == 0.3
    assert loaded.weak_point_status["feedback"] == "active"
    assert loaded.weak_point_confidence["feedback"] == 0.8


def test_load_state_default_when_missing(tmp_path):
    loaded = load_state(tmp_path / "missing.json")
    assert loaded.major == ""
