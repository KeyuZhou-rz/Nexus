from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nexus.cli import act
from nexus.cli import next_action as home_cli
from nexus.core.action_service import ActionService
from nexus.core.storage import save_feeds
from nexus.core.models import FeedSource
from nexus.core.v1_config import V1_TZ
from nexus.demo import create_demo_runtime


@pytest.fixture()
def demo_dir(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    create_demo_runtime(root)
    (root / "sync_receipt.json").unlink(missing_ok=True)
    return root


def _write_receipt(root: Path, finished_at: datetime) -> None:
    (root / "sync_receipt.json").write_text(
        json.dumps({"status": "succeeded", "finished_at": finished_at.isoformat()}),
        encoding="utf-8",
    )


def test_needs_sync_only_when_a_source_exists_and_state_is_stale(tmp_path: Path) -> None:
    assert home_cli.needs_sync(tmp_path) is False

    feed = FeedSource(kind="ical_file", name="OS", url="file:///tmp/os.ics")
    save_feeds([feed], tmp_path / "feeds.json")
    assert home_cli.needs_sync(tmp_path) is True

    now = datetime.now(timezone.utc)
    _write_receipt(tmp_path, now + timedelta(seconds=5))
    assert home_cli.needs_sync(tmp_path, now=now) is False
    assert home_cli.needs_sync(tmp_path, now=now + timedelta(hours=3)) is True


def test_needs_sync_after_feeds_change(tmp_path: Path) -> None:
    _write_receipt(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=5))
    save_feeds([FeedSource(kind="ical_file", name="OS", url="file:///tmp/os.ics")], tmp_path / "feeds.json")
    assert home_cli.needs_sync(tmp_path) is True


def test_bare_nexus_refreshes_then_shows_one_task(demo_dir: Path, capsys) -> None:
    assert home_cli.home(["--data-dir", str(demo_dir)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("现在做  ")
    assert "之后" in out
    assert "nexus start" in out
    assert (demo_dir / "sync_receipt.json").is_file()
    assert (demo_dir / "judgment.json").is_file()


def test_offline_flag_skips_refresh(demo_dir: Path, capsys) -> None:
    assert home_cli.home(["--data-dir", str(demo_dir), "--offline"]) == 0
    assert not (demo_dir / "sync_receipt.json").exists()
    assert "现在做" in capsys.readouterr().out


def test_start_and_done_bind_to_the_shown_task(demo_dir: Path, capsys) -> None:
    shown = home_cli.persist_next_action(data_dir=demo_dir)["obligation_id"]

    assert act.start(["--data-dir", str(demo_dir)]) == 0
    running = ActionService(demo_dir).running_action()
    assert running["obligation_id"] == shown
    assert "正在做" in capsys.readouterr().out

    assert act.done(["--data-dir", str(demo_dir)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"完成「{running['title']}」")
    assert ActionService(demo_dir).running_action() is None
    assert ActionService(demo_dir).store.get_obligation(shown)["status"] == "done"
    assert home_cli.persist_next_action(data_dir=demo_dir)["obligation_id"] != shown


def test_start_refuses_a_second_running_task(demo_dir: Path, capsys) -> None:
    assert act.start(["--data-dir", str(demo_dir)]) == 0
    assert act.start(["Physics", "--data-dir", str(demo_dir)]) == 1
    assert "先 nexus pause" in capsys.readouterr().err
    assert act.pause(["--data-dir", str(demo_dir)]) == 0
    assert ActionService(demo_dir).running_action() is None
    assert act.pause(["--data-dir", str(demo_dir)]) == 1


def test_keyword_must_match_exactly_one_open_task(demo_dir: Path, capsys) -> None:
    assert act.done(["no-such-task", "--data-dir", str(demo_dir)]) == 1
    assert act.done(["Operating Systems", "--data-dir", str(demo_dir)]) == 1
    err = capsys.readouterr().err
    assert "没有找到" in err
    assert "再具体一点" in err
    assert act.done(["Physics", "--data-dir", str(demo_dir)]) == 0


def test_add_creates_a_manual_task_with_local_deadline(tmp_path: Path, capsys) -> None:
    assert act.add(["准备展示", "--due", "2026-10-23 18:00", "--course", "Seminar", "--hours", "2",
                    "--data-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("加上了「准备展示」")
    row = next(
        row for row in ActionService(tmp_path).store.list_obligations() if row["title"] == "准备展示"
    )
    assert row["course"] == "Seminar"
    assert row["remaining_hours"] == 2
    due = datetime.fromisoformat(row["due_at"]).astimezone(V1_TZ)
    assert (due.month, due.day, due.hour, due.minute) == (10, 23, 18, 0)


def test_short_due_dates_default_to_end_of_day_and_roll_forward() -> None:
    now = datetime(2026, 12, 30, 9, 0, tzinfo=V1_TZ)
    assert act._parse_due("12-31", now) == datetime(2026, 12, 31, 23, 59, tzinfo=V1_TZ)
    assert act._parse_due("01-05", now) == datetime(2027, 1, 5, 23, 59, tzinfo=V1_TZ)
    with pytest.raises(ValueError):
        act._parse_due("13-45", now)


def test_empty_runtime_explains_how_to_start(tmp_path: Path, capsys) -> None:
    assert home_cli.home(["--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "现在没有要做的事" in out
    assert "nexus init --feed-url" in out
    assert "nexus add" in out


def test_cli_help_separates_the_product_from_extensions() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "nexus.cli.main", "--help"],
        text=True, capture_output=True, check=False, env={**os.environ},
    )
    assert result.returncode == 0
    core, _, extensions = result.stdout.partition("frozen extensions")
    for name in ("start", "pause", "done", "add", "dashboard", "sync"):
        assert f"  {name} " in core
    assert "  ingest " in extensions
    assert "  briefing " in extensions
