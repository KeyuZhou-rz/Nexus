from __future__ import annotations

from nexus.schemas import validate_task_dict


def test_validate_task_dict_ok():
    payload = {
        "id": "t1",
        "title": "Task",
        "source": "gmail",
        "status": "open",
        "due_at": None,
        "received_at": None,
        "tags": ["email"],
    }
    assert validate_task_dict(payload) == []


def test_validate_task_dict_invalid_fields():
    payload = {
        "id": "",
        "title": "",
        "source": "",
        "status": "bad",
        "due_at": "not-a-date",
        "received_at": "still-not-a-date",
        "tags": "not-a-list",
    }
    errors = validate_task_dict(payload)
    assert len(errors) >= 6
