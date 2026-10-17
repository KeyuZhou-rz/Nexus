from __future__ import annotations

import stat
from datetime import datetime, timedelta
from pathlib import Path

from nexus.cli import next_action as home_cli
from nexus.cli import week as week_cli
from nexus.core.academic_state_service import AcademicStateService
from nexus.core.action_service import ActionService
from nexus.core.today import build_today
from nexus.core.usage_log import SHOWN_LOG, load_shown, record_shown
from nexus.core.v1_config import V1_NOW, V1_TZ
from nexus.core.week import build_week


def _add(root: Path, title: str, due: datetime, *, now: datetime = V1_NOW) -> str:
    return AcademicStateService(root).add_manual_obligation(
        title=title, due_at=due, remaining_hours=2, now=now,
    )["id"]


def test_untouched_work_overdue_for_a_day_waits_for_confirmation(tmp_path: Path) -> None:
    stale = _add(tmp_path, "Lab 1", V1_NOW - timedelta(days=3))
    recent = _add(tmp_path, "Lab 2", V1_NOW - timedelta(hours=3))
    upcoming = _add(tmp_path, "Lab 3", V1_NOW + timedelta(days=2))

    today = build_today(tmp_path, now=V1_NOW)

    assert today.overdue_unconfirmed == (stale,)
    assert today.recommendation.obligation_id == recent
    assert upcoming not in today.overdue_unconfirmed
    assert today.to_dict()["overdue_unconfirmed"] == [stale]


def test_working_on_overdue_work_brings_it_back(tmp_path: Path) -> None:
    stale = _add(tmp_path, "Lab 1", V1_NOW - timedelta(days=3))
    _add(tmp_path, "Lab 3", V1_NOW + timedelta(days=2))
    actions = ActionService(tmp_path)
    actions.start(stale, now=V1_NOW - timedelta(hours=2))
    actions.stop(stale, now=V1_NOW - timedelta(hours=1))

    today = build_today(tmp_path, now=V1_NOW)

    assert today.overdue_unconfirmed == ()
    assert today.recommendation.obligation_id == stale


def test_only_stale_overdue_work_leaves_no_pick(tmp_path: Path) -> None:
    stale = _add(tmp_path, "Lab 1", V1_NOW - timedelta(days=3))

    today = build_today(tmp_path, now=V1_NOW)

    assert today.overdue_unconfirmed == (stale,)
    assert not (today.recommendation and today.recommendation.obligation_id)
    assert today.missing_input == "overdue_unconfirmed"


def test_terminal_asks_about_stale_work_instead_of_listing_it(tmp_path: Path) -> None:
    now = datetime.now(V1_TZ)
    _add(tmp_path, "Submitted essay", now - timedelta(days=4), now=now)
    _add(tmp_path, "Problem set", now + timedelta(days=1), now=now)

    out = home_cli.render(tmp_path, home_cli.persist_next_action(data_dir=tmp_path))

    assert out.startswith("现在做  Problem set")
    assert "已过截止  Submitted essay · 交了就 nexus done 关键词" in out
    assert "之后" not in out


def test_shown_log_dedupes_repeats_and_stays_private(tmp_path: Path) -> None:
    assert record_shown(tmp_path, "ob:a", surface="cli", now=V1_NOW) is True
    assert record_shown(tmp_path, "ob:a", surface="dashboard", now=V1_NOW + timedelta(minutes=10)) is False
    assert record_shown(tmp_path, "ob:b", surface="cli", now=V1_NOW + timedelta(minutes=11)) is True
    assert record_shown(tmp_path, "ob:a", surface="cli", now=V1_NOW + timedelta(minutes=50)) is True

    assert [entry["obligation_id"] for entry in load_shown(tmp_path)] == ["ob:a", "ob:b", "ob:a"]
    assert stat.S_IMODE((tmp_path / SHOWN_LOG).stat().st_mode) == 0o600


def test_render_logs_the_recommendation_but_not_a_running_task(tmp_path: Path) -> None:
    oid = _add(tmp_path, "Problem set", datetime.now(V1_TZ) + timedelta(days=1))
    home_cli.render(tmp_path, home_cli.persist_next_action(data_dir=tmp_path))
    assert [entry["obligation_id"] for entry in load_shown(tmp_path)] == [oid]

    (tmp_path / SHOWN_LOG).unlink()
    ActionService(tmp_path).start(oid)
    home_cli.render(tmp_path, home_cli.persist_next_action(data_dir=tmp_path))
    assert load_shown(tmp_path) == []


def test_week_measures_use_follow_through_and_estimates(tmp_path: Path) -> None:
    start = V1_NOW - timedelta(days=2)
    followed = _add(tmp_path, "Problem set", V1_NOW + timedelta(days=1), now=start)
    ignored = _add(tmp_path, "Reading", V1_NOW + timedelta(days=3), now=start)
    untimed = _add(tmp_path, "Quiz prep", V1_NOW + timedelta(days=2), now=start)
    actions = ActionService(tmp_path)

    record_shown(tmp_path, followed, surface="cli", now=start)
    actions.start(followed, now=start + timedelta(hours=1))
    actions.complete(followed, now=start + timedelta(hours=2, minutes=30))
    record_shown(tmp_path, ignored, surface="cli", now=V1_NOW - timedelta(hours=5))
    actions.complete(untimed, now=V1_NOW - timedelta(hours=1))

    report = build_week(tmp_path, now=V1_NOW)

    assert report["days_used"] == 2
    assert report["shown"] == 2
    assert report["followed"] == 1
    assert report["completed"] == 2
    assert report["completed_recommended"] == 1
    assert report["completed_without_timer"] == 1
    assert report["manual_added"] == 3
    assert report["measured"] == 1
    assert report["actual_hours"] == 1.5

    text = week_cli.render(report)
    assert "用了    2/7 天" in text
    assert "推荐    2 件，1 件随后开始或完成（50%）" in text
    assert "1 件没计时" in text
    assert "实测 1 件" in text


def test_week_outside_the_window_is_empty(tmp_path: Path) -> None:
    oid = _add(tmp_path, "Old work", V1_NOW - timedelta(days=20), now=V1_NOW - timedelta(days=30))
    record_shown(tmp_path, oid, surface="cli", now=V1_NOW - timedelta(days=30))
    report = build_week(tmp_path, now=V1_NOW)
    assert report["days_used"] == 0
    assert "还没有记录" in week_cli.render(report)
