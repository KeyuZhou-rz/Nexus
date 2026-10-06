"""Synthetic V1 capacity benchmark: run with PYTHONPATH=src python benchmarks/today_capacity.py."""

import json
from datetime import timedelta
from statistics import median
from time import perf_counter

from nexus.core.today import evaluate_state
from nexus.core.v1_config import V1_NOW, V1_TIMEZONE_NAME

state = {
    "revision": 1,
    "availability_rules": [
        {
            "id": str(i),
            "weekday": i,
            "starts_local": "09:00",
            "ends_local": "22:00",
            "timezone": V1_TIMEZONE_NAME,
            "enabled": True,
        }
        for i in range(7)
    ],
    "calendar_blocks": [
        {
            "id": str(i),
            "source": "synthetic",
            "external_id": str(i),
            "title": "Busy",
            "starts_at": (V1_NOW + timedelta(minutes=i * 30)).isoformat(),
            "ends_at": (V1_NOW + timedelta(minutes=i * 30 + 15)).isoformat(),
        }
        for i in range(1000)
    ],
    "obligations": [
        {
            "id": str(i),
            "title": "Synthetic",
            "status": "open",
            "kind": "assignment",
            "due_at": (V1_NOW + timedelta(days=14, minutes=i)).isoformat(),
            "estimated_hours": 3,
            "spent_hours": 0,
        }
        for i in range(500)
    ],
}
evaluate_state(state, now=V1_NOW)
elapsed = []
for _ in range(5):
    start = perf_counter()
    result = evaluate_state(state, now=V1_NOW)
    assert len(result[0]) == 500
    elapsed.append((perf_counter() - start) * 1000)
report = {
    "obligations": 500,
    "calendar_blocks": 1000,
    "distinct_deadlines": 500,
    "median_ms": round(median(elapsed), 1),
    "budget_ms": 500,
}
print(json.dumps(report))
assert report["median_ms"] < report["budget_ms"], report
