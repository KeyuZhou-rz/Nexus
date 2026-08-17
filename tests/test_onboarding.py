from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from nexus.models import FeedSource
from nexus.onboarding import connect_calendar_feed, initialize_runtime, upsert_feed, validate_feed


def test_initialize_runtime_is_idempotent_and_preserves_existing_data(tmp_path: Path) -> None:
    first = initialize_runtime(tmp_path)
    (tmp_path / "tasks.json").write_text('[{"id": "keep"}]', encoding="utf-8")
    second = initialize_runtime(tmp_path)

    assert first["created"] == ["feeds.json", "tasks.json"]
    assert second["created"] == []
    assert json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8")) == [{"id": "keep"}]


def test_initialize_runtime_creates_owner_only_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "runtime"

    initialize_runtime(data_dir)

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700


def test_initialize_runtime_preserves_existing_directory_permissions(tmp_path: Path) -> None:
    data_dir = tmp_path / "shared-runtime"
    data_dir.mkdir(mode=0o750)
    data_dir.chmod(0o750)

    initialize_runtime(data_dir)

    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o750


def test_initialize_runtime_does_not_chmod_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "managed-elsewhere"
    target.mkdir(mode=0o750)
    target.chmod(0o750)
    data_dir = tmp_path / "runtime-link"
    data_dir.symlink_to(target, target_is_directory=True)

    initialize_runtime(data_dir)

    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_upsert_feed_updates_same_url_and_preserves_other_feeds(tmp_path: Path) -> None:
    first = FeedSource("brightspace_ical", "OS", "https://example.edu/os.ics", "OS")
    other = FeedSource("brightspace_rss", "News", "https://example.edu/news.xml", "NEWS")
    upsert_feed(tmp_path, first)
    upsert_feed(tmp_path, other)
    result = upsert_feed(
        tmp_path,
        FeedSource("brightspace_ical", "Operating Systems", first.url, "CS202_OS"),
    )

    payload = json.loads((tmp_path / "feeds.json").read_text(encoding="utf-8"))
    assert result["action"] == "updated"
    assert result["feed_count"] == 2
    assert payload[0]["name"] == "Operating Systems"
    assert payload[1]["name"] == "News"


def test_connect_calendar_feed_syncs_without_google(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": {"task_count": 1}}

    result = connect_calendar_feed(
        tmp_path,
        FeedSource("brightspace_ical", "Circuits", "https://example.edu/cal.ics", "EE251"),
        sync=fake_sync,
    )

    assert result["feed"]["action"] == "created"
    assert calls == [{"data_dir": tmp_path, "trigger": "setup", "include_google": False}]
    saved = json.loads((tmp_path / "feeds.json").read_text(encoding="utf-8"))
    assert saved[0]["course"] == "EE251"


def test_validate_feed_rejects_insecure_remote_urls() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_feed(FeedSource("brightspace_ical", "OS", "http://example.edu/feed.ics"))
    with pytest.raises(ValueError, match="must use HTTPS"):
        validate_feed(FeedSource("brightspace_rss", "OS", "/tmp/feed.xml"))
