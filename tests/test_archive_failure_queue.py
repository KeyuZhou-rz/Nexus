from __future__ import annotations

from nexus.archive_sync.reporting import update_failure_queue, load_failure_queue


def test_update_failure_queue_dedup_and_resolve(tmp_path):
    path = tmp_path / "archive_failures.json"

    update_failure_queue(
        path,
        [
            {
                "course": "OS",
                "assignment_title": "A1",
                "attachment_url": "https://x/a1.pdf",
                "error": "timeout",
            }
        ],
    )

    report = update_failure_queue(
        path,
        [
            {
                "course": "OS",
                "assignment_title": "A2",
                "attachment_url": "https://x/a2.pdf",
                "error": "403",
            }
        ],
        resolved_attachment_urls={"https://x/a1.pdf"},
    )

    assert report["failure_count"] == 1
    items = load_failure_queue(path)
    assert len(items) == 1
    assert items[0]["attachment_url"] == "https://x/a2.pdf"
