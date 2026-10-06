from __future__ import annotations

from nexus.core import aggregation
from nexus.core.config import AppConfig
from nexus.core.failure_queue import load_failure_queue, update_failure_queue


def test_generic_failures_dead_letter_after_bounded_retries(tmp_path) -> None:
    path = tmp_path / "ingest_failures.json"
    failure = {
        "operation": "ingest",
        "course_id": "CS101",
        "input_path": "lecture.pdf",
        "error": "parser timeout",
    }

    update_failure_queue(path, [failure], max_retries=1)
    update_failure_queue(path, [failure], max_retries=1)
    report = update_failure_queue(path, [failure], max_retries=1)

    assert load_failure_queue(path) == []
    dead = load_failure_queue(tmp_path / "ingest_failures.json.dead")
    assert len(dead) == 1
    assert dead[0]["retry_count"] == 2
    assert dead[0]["manual_review_required"] is True
    assert report["manual_review_required"] is True


def test_successful_generic_key_clears_pending_failure(tmp_path) -> None:
    path = tmp_path / "aggregation_failures.json"
    failure = {"operation": "aggregation", "source": "google", "error": "timeout"}
    update_failure_queue(path, [failure])
    report = update_failure_queue(path, [], resolved_attachment_urls={"aggregation||source:google"})

    assert report["failure_count"] == 0
    assert load_failure_queue(path) == []


def test_aggregation_queues_and_resolves_source_failures(tmp_path, monkeypatch) -> None:
    class Feed:
        kind = "brightspace_ical"
        name = "Course"
        url = "file:///feed.ics"
        enabled = True
        course = "CS101"
        audience = "ui"
        mode = "default"

    class FailingAggregator:
        def __init__(self, *_args):
            pass

        def fetch_tasks(self):
            raise RuntimeError("feed timeout")

    class HealthyAggregator:
        def __init__(self, *_args):
            pass

        def fetch_tasks(self):
            return []

    monkeypatch.setattr(aggregation, "load_feeds", lambda _path: [Feed()])
    monkeypatch.setattr(aggregation, "load_tasks", lambda _path: [])
    monkeypatch.setattr(aggregation, "save_tasks", lambda *_args: None)
    monkeypatch.setattr(aggregation, "BrightspaceAggregator", FailingAggregator)
    config = AppConfig(data_dir=tmp_path)

    failed = aggregation.run_aggregation(
        include_google=False, include_canvas=False, data_dir=tmp_path, config=config
    )
    assert failed.failure_queue and failed.failure_queue["failure_count"] == 1
    assert load_failure_queue(tmp_path / "aggregation_failures.json")[0]["source"] == "brightspace"

    monkeypatch.setattr(aggregation, "BrightspaceAggregator", HealthyAggregator)
    recovered = aggregation.run_aggregation(
        include_google=False, include_canvas=False, data_dir=tmp_path, config=config
    )
    assert recovered.failure_queue and recovered.failure_queue["failure_count"] == 0
