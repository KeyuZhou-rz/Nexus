"""Regressions found by the V1 release audit; all data is synthetic."""

from datetime import timedelta
import sqlite3

import pytest

from nexus.core.academic_state import refresh_from_snapshot, tasks_from_obligations
from nexus.core.academic_state_service import AcademicStateService
from nexus.core.action_service import ActionService
from nexus.core.context_api import build_context
from nexus.knowledge.sqlite_store import connect
from nexus.core.models import Task
from nexus.core.storage import save_tasks
from nexus.core.today import build_today
from nexus.core.v1_config import V1_NOW, V1_TIMEZONE_NAME


def seed(root, *, title="Homework", remaining=6):
    service = AcademicStateService(root)
    task = Task(
        id="lms:one",
        title=title,
        course="CS",
        source="brightspace",
        due_at=V1_NOW + timedelta(days=7),
        tags=["assignment"],
    )
    service.ingest_snapshot([task], now=V1_NOW)
    service.update_obligation(task.obligation_id, remaining_hours=remaining, now=V1_NOW)
    save_tasks([task], root / "tasks.json")
    return service, task, ActionService(root)


def test_page_refresh_is_read_only_and_stale_sync_preserves_manual_deadline(tmp_path):
    service, task, _ = seed(tmp_path)
    moved = V1_NOW + timedelta(days=1)
    service.update_obligation(task.obligation_id, due_at=moved, now=V1_NOW)
    revision = service.store.get_state_revision()
    refresh_from_snapshot(tmp_path, [task])
    assert service.store.get_state_revision() == revision
    service.ingest_snapshot([task], now=V1_NOW)
    assert (
        service.store.get_obligation(task.obligation_id)["due_at"] == moved.isoformat()
    )
    task.due_at = V1_NOW + timedelta(days=2)
    service.ingest_snapshot([task], now=V1_NOW)
    assert (
        service.store.get_obligation(task.obligation_id)["due_at"]
        == task.due_at.isoformat()
    )


def test_stop_reload_resume_learns_total_work_and_excludes_break(tmp_path):
    service, task, actions = seed(tmp_path)
    oid = task.obligation_id
    actions.start(oid, now=V1_NOW)
    service.ingest_snapshot([task], now=V1_NOW + timedelta(minutes=15))
    actions.stop(oid, now=V1_NOW + timedelta(minutes=30))
    service.ingest_snapshot([task], now=V1_NOW + timedelta(hours=3))
    row = service.store.get_obligation(oid)
    assert row["spent_hours"] == 0.5
    assert row["remaining_override_hours"] == 5.5
    actions.start(oid, now=V1_NOW + timedelta(hours=3))
    actions.complete(oid, now=V1_NOW + timedelta(hours=4))
    assert service.store.get_obligation(oid)["spent_hours"] == 1.5
    assert service.store.list_effort_samples()[0]["minutes"] == 90


def test_completion_failure_rolls_back_event_status_sample_and_allows_retry(tmp_path):
    service, task, actions = seed(tmp_path)
    with connect(service.store.db_path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_sample BEFORE INSERT ON effort_samples BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="interrupted"):
        actions.complete(task.obligation_id, effort_minutes=90, now=V1_NOW)
    assert service.store.get_obligation(task.obligation_id)["status"] == "open"
    assert service.store.list_action_events() == []
    assert service.store.list_effort_samples() == []
    with connect(service.store.db_path) as conn:
        conn.execute("DROP TRIGGER fail_sample")
    actions.complete(task.obligation_id, effort_minutes=90, now=V1_NOW)
    assert service.store.list_effort_samples()[0]["minutes"] == 90


def test_same_title_new_instance_does_not_inherit_completion(tmp_path):
    service, task, actions = seed(tmp_path)
    original = task.obligation_id
    actions.complete(original, now=V1_NOW)
    fresh = Task(
        id="lms:next",
        title=task.title,
        course=task.course,
        source=task.source,
        due_at=V1_NOW + timedelta(days=14),
        tags=task.tags,
    )
    service.ingest_snapshot([fresh], now=V1_NOW + timedelta(days=7))
    assert fresh.obligation_id != original
    assert service.store.get_obligation(fresh.obligation_id)["status"] == "open"
    projected = tasks_from_obligations(tmp_path, [fresh])
    assert projected[0].obligation_id == fresh.obligation_id


def test_simultaneous_repeated_titles_are_separate_and_uid_rename_is_stable(tmp_path):
    service, task, _ = seed(tmp_path)
    other = Task(
        id="lms:two",
        title=task.title,
        course=task.course,
        source=task.source,
        due_at=task.due_at + timedelta(days=7),
        tags=task.tags,
    )
    service.ingest_snapshot([task, other], now=V1_NOW)
    assert other.obligation_id != task.obligation_id
    other.title = "Renamed Homework"
    other.due_at += timedelta(days=1)
    service.ingest_snapshot([task, other], now=V1_NOW)
    assert len(service.store.list_obligations()) == 2
    assert service.store.get_obligation(other.obligation_id)["title"] == other.title


def test_context_and_today_agree_when_risk_beats_deadline(tmp_path):
    service, task, _ = seed(tmp_path)
    task.due_at = V1_NOW + timedelta(days=3)
    service.ingest_snapshot([task], now=V1_NOW)
    service.add_manual_obligation(
        title="History reading",
        course="History",
        due_at=V1_NOW + timedelta(days=1),
        remaining_hours=1,
        now=V1_NOW,
    )
    service.add_availability_rule(
        weekday=0, starts_local="16:00", ends_local="18:00", timezone=V1_TIMEZONE_NAME
    )
    today = build_today(tmp_path, now=V1_NOW)
    context = build_context(data_dir=tmp_path, now=V1_NOW)
    assert today.recommendation.obligation_id == task.obligation_id
    assert context["next_action"]["obligation_id"] == task.obligation_id
    assert context["next_action"]["risk_band"] == "high"
    assert context["today"]["recommendation"] == today.recommendation.to_dict()


def test_defer_reranks_without_lowering_risk_and_expires(tmp_path):
    service, task, actions = seed(tmp_path)
    other = service.add_manual_obligation(
        title="Other assignment",
        course="CS",
        due_at=task.due_at + timedelta(days=1),
        now=V1_NOW,
    )
    before = build_today(tmp_path, now=V1_NOW)
    actions.defer(
        task.obligation_id,
        reason="break",
        next_review_at=V1_NOW + timedelta(hours=1),
        now=V1_NOW,
    )
    after = build_today(tmp_path, now=V1_NOW)
    assert after.recommendation.obligation_id == other["id"]
    risk = next(
        item for item in after.assessments if item.obligation_id == task.obligation_id
    )
    assert risk.band == before.assessments[0].band
    assert (
        build_today(
            tmp_path, now=V1_NOW + timedelta(hours=1)
        ).recommendation.obligation_id
        == task.obligation_id
    )


def test_manual_obligation_survives_complete_external_snapshot(tmp_path):
    service = AcademicStateService(tmp_path)
    row = service.add_manual_obligation(title="Essay", now=V1_NOW)
    service.ingest_snapshot([], reconcile_missing=True, now=V1_NOW)
    assert service.store.get_obligation(row["id"])["status"] == "open"
    assert (tmp_path / "tasks.json").is_file()


def test_successful_calendar_sync_changes_capacity_and_cancellation_restores_it(
    monkeypatch, tmp_path
):
    from nexus.core.aggregation import run_aggregation
    from nexus.core.aggregators.google import GoogleAggregator
    from nexus.core.config import AppConfig

    service, task, _ = seed(tmp_path)
    service.update_obligation(
        task.obligation_id, due_at=V1_NOW + timedelta(days=1), now=V1_NOW
    )
    service.add_availability_rule(
        weekday=0, starts_local="16:00", ends_local="22:00", timezone=V1_TIMEZONE_NAME
    )
    credentials, token = tmp_path / "client.json", tmp_path / "token.json"
    credentials.write_text("{}")
    token.write_text("{}")
    config = AppConfig(
        data_dir=tmp_path, google_credentials_path=credentials, google_token_path=token
    )
    event = Task(
        id="gcal:class",
        title="Class",
        course="Calendar",
        source="gcal",
        due_at=V1_NOW.replace(hour=17),
        starts_at=V1_NOW.replace(hour=17),
        ends_at=V1_NOW.replace(hour=20),
        tags=["calendar"],
    )
    monkeypatch.setattr(GoogleAggregator, "fetch_tasks", lambda self: [event])
    result = run_aggregation(data_dir=tmp_path, config=config, include_canvas=False)
    assert result.errors == []
    risk = build_today(tmp_path, now=V1_NOW).assessments[0]
    assert risk.available_hours == 2.5
    assert risk.band == "high"
    assert "calendar_conflict" in risk.reasons

    def failed(self):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(GoogleAggregator, "fetch_tasks", failed)
    result = run_aggregation(data_dir=tmp_path, config=config, include_canvas=False)
    assert result.source_status["google"] == "failed"
    assert build_today(tmp_path, now=V1_NOW).assessments[0].available_hours == 2.5

    monkeypatch.setattr(GoogleAggregator, "fetch_tasks", lambda self: [])
    result = run_aggregation(data_dir=tmp_path, config=config, include_canvas=False)
    assert result.errors == []
    assert build_today(tmp_path, now=V1_NOW).assessments[0].available_hours == 6
    assert service.store.list_calendar_blocks()[0]["status"] == "cancelled"


def test_google_calendar_pagination_stable_identity_and_duration(tmp_path):
    from unittest.mock import MagicMock
    from nexus.core.aggregators.google import GoogleAggregator
    from nexus.core.config import AppConfig

    api = MagicMock()
    api.calendarList().list().execute.return_value = {
        "items": [{"id": "primary", "primary": True, "summary": "Calendar"}]
    }
    first = {
        "id": "one",
        "summary": "Class",
        "start": {"dateTime": "2026-09-07T17:00:00-04:00"},
        "end": {"dateTime": "2026-09-07T20:00:00-04:00"},
    }
    second = {**first, "id": "two", "transparency": "transparent"}
    api.events().list().execute.side_effect = [
        {"items": [first], "nextPageToken": "next"},
        {"items": [second]},
    ]
    aggregator = GoogleAggregator(
        config=AppConfig(
            data_dir=tmp_path,
            google_credentials_path=tmp_path / "client.json",
            google_token_path=tmp_path / "token.json",
        )
    )
    tasks = list(aggregator._fetch_calendar(api))
    assert len(tasks) == 2
    assert (tasks[0].ends_at - tasks[0].starts_at).total_seconds() == 3 * 3600
    assert tasks[1].calendar_kind == "free"
    calls = api.events().list.call_args_list
    assert calls[-1].kwargs["pageToken"] == "next"
    moved = {**first, "start": {"dateTime": "2026-09-08T17:00:00-04:00"}}
    assert aggregator._calendar_task("primary", "Calendar", moved).id == tasks[0].id
    saved = Task.from_dict(tasks[0].to_dict())
    assert saved.starts_at == tasks[0].starts_at and saved.ends_at == tasks[0].ends_at


def test_workspace_stop_resume_complete_and_defer_use_the_same_obligation(
    monkeypatch, tmp_path
):
    from datetime import datetime
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import nexus.core.dashboard.app as dashboard
    import nexus.core.action_service as actions_module

    service, task, _ = seed(tmp_path)
    second = service.add_manual_obligation(
        title="Other assignment",
        course="CS",
        due_at=task.due_at + timedelta(days=1),
        now=V1_NOW,
    )
    clock = [V1_NOW]

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0]

    monkeypatch.setattr(dashboard, "datetime", FrozenDatetime)
    monkeypatch.setattr(actions_module, "_resolve_now", lambda now: now or clock[0])
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app_path = Path(__file__).parents[1] / "src/nexus/core/dashboard_app.py"
    app = AppTest.from_file(app_path).run(timeout=30)
    assert not app.exception
    ActionService(tmp_path).defer(task.obligation_id, reason="Later", now=clock[0])
    app.run()
    assert (
        build_today(tmp_path, now=clock[0]).recommendation.obligation_id == second["id"]
    )
    clock[0] += timedelta(hours=1)
    app.run()
    next(button for button in app.button if button.label == "开始做").click().run()
    clock[0] += timedelta(minutes=30)
    next(button for button in app.button if button.label == "暂停").click().run()
    clock[0] += timedelta(hours=2)
    app.run()
    assert service.store.get_obligation(task.obligation_id)["spent_hours"] == 0.5
    next(button for button in app.button if button.label == "开始做").click().run()
    clock[0] += timedelta(minutes=60)
    next(button for button in app.button if button.label == "完成这件").click().run()
    assert not app.exception
    assert service.store.get_obligation(task.obligation_id)["status"] == "done"
    assert service.store.list_effort_samples()[0]["minutes"] == 90
    restarted = AppTest.from_file(app_path).run(timeout=30)
    assert not restarted.exception
    assert (
        build_today(tmp_path, now=clock[0]).recommendation.obligation_id == second["id"]
    )


def test_stable_provider_uid_new_week_does_not_inherit_unfinished_work(tmp_path):
    service, task, actions = seed(tmp_path)
    task.external_id_stable = True
    actions.start(task.obligation_id, now=V1_NOW)
    actions.stop(task.obligation_id, now=V1_NOW + timedelta(hours=2))
    fresh = Task(
        id="lms:next",
        title=task.title,
        course=task.course,
        source=task.source,
        due_at=task.due_at + timedelta(days=7),
        tags=task.tags,
        external_id_stable=True,
    )
    service.ingest_snapshot([fresh], now=V1_NOW, reconcile_missing=True)
    assert fresh.obligation_id != task.obligation_id
    assert service.store.get_obligation(fresh.obligation_id)["spent_hours"] == 0


def test_source_title_change_preserves_manual_deadline(tmp_path):
    service, task, _ = seed(tmp_path)
    manual_due = V1_NOW + timedelta(days=1)
    service.update_obligation(task.obligation_id, due_at=manual_due, now=V1_NOW)
    task.title = "Renamed Homework"
    service.ingest_snapshot([task], now=V1_NOW)
    row = service.store.get_obligation(task.obligation_id)
    assert row["title"] == task.title
    assert row["due_at"] == manual_due.isoformat()


def test_resuming_after_defer_with_different_timezone_offsets(tmp_path):
    from datetime import timezone

    service, task, actions = seed(tmp_path)
    actions.defer(
        task.obligation_id, reason="break", now=V1_NOW.astimezone(timezone.utc)
    )
    actions.record_event(
        task.obligation_id, "resumed", now=V1_NOW + timedelta(minutes=30)
    )
    today = build_today(tmp_path, now=V1_NOW + timedelta(minutes=30))
    assert not today.deferred_until
    assert today.recommendation.obligation_id == task.obligation_id


def test_cancelled_calendar_obligation_leaves_today_and_can_be_restored(tmp_path):
    service, task, _ = seed(tmp_path)
    task.calendar_status = "cancelled"
    service.ingest_snapshot([task], now=V1_NOW)
    assert service.store.get_obligation(task.obligation_id)["status"] == "cancelled"
    assert not build_today(tmp_path, now=V1_NOW).recommendation.obligation_id
    task.calendar_status = "confirmed"
    service.ingest_snapshot([task], now=V1_NOW)
    assert (
        build_today(tmp_path, now=V1_NOW).recommendation.obligation_id
        == task.obligation_id
    )


def test_legacy_timer_can_stop_and_complete_without_counting_the_break(tmp_path):
    from nexus.core.task_service import save_completions

    service, task, actions = seed(tmp_path)
    save_completions(tmp_path, {task.id: {"started_at": V1_NOW.isoformat()}})
    service.import_legacy_completions(now=V1_NOW)
    assert actions.stop(task.obligation_id, now=V1_NOW + timedelta(minutes=30))[
        "inserted"
    ]
    assert service.store.get_obligation(task.obligation_id)["spent_hours"] == 0.5
    actions.complete(task.obligation_id, now=V1_NOW + timedelta(hours=3))
    assert service.store.list_effort_samples()[0]["minutes"] == 30


def test_legacy_completion_import_rolls_back_and_retries_as_one_transaction(tmp_path):
    from nexus.core.task_service import save_completions

    service, task, _ = seed(tmp_path)
    save_completions(
        tmp_path, {task.id: {"done_at": V1_NOW.isoformat(), "effort_minutes": 45}}
    )
    with connect(service.store.db_path) as conn:
        conn.execute(
            "CREATE TRIGGER fail_sample BEFORE INSERT ON effort_samples BEGIN SELECT RAISE(ABORT, 'interrupted'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="interrupted"):
        service.import_legacy_completions(now=V1_NOW)
    assert service.store.list_action_events() == []
    assert service.store.get_obligation(task.obligation_id)["status"] == "open"
    with connect(service.store.db_path) as conn:
        conn.execute("DROP TRIGGER fail_sample")
    service.import_legacy_completions(now=V1_NOW)
    assert service.store.list_effort_samples()[0]["minutes"] == 45
    assert service.store.get_obligation(task.obligation_id)["spent_hours"] == 0.75


def test_existing_database_imports_legacy_completion_on_first_refresh(tmp_path):
    from nexus.core.task_service import save_completions

    service, task, _ = seed(tmp_path)
    save_completions(
        tmp_path, {task.id: {"done_at": V1_NOW.isoformat(), "effort_minutes": 45}}
    )
    refresh_from_snapshot(tmp_path, [task])
    assert service.store.get_obligation(task.obligation_id)["status"] == "done"
    assert service.store.list_effort_samples()[0]["minutes"] == 45


def test_old_started_event_without_metadata_can_be_stopped(tmp_path):
    service, task, actions = seed(tmp_path)
    service.store.insert_action_event(
        event_id="old-start",
        obligation_id=task.obligation_id,
        event_type="started",
        occurred_at=V1_NOW.isoformat(),
    )
    assert actions.stop(task.obligation_id, now=V1_NOW + timedelta(minutes=30))[
        "inserted"
    ]
    assert service.store.get_obligation(task.obligation_id)["spent_hours"] == 0.5


def test_cancelled_running_obligation_does_not_block_starting_another(tmp_path):
    service, task, actions = seed(tmp_path)
    other = service.add_manual_obligation(
        title="Other homework", course="CS", now=V1_NOW
    )
    actions.start(task.obligation_id, now=V1_NOW)
    task.calendar_status = "cancelled"
    service.ingest_snapshot([task], now=V1_NOW)
    assert actions.running_action(now=V1_NOW) is None
    assert actions.start(other["id"], now=V1_NOW + timedelta(minutes=1))["inserted"]


def test_secondary_source_update_preserves_manual_deadline(tmp_path):
    service, task, _ = seed(tmp_path)
    manual_due = V1_NOW + timedelta(days=1)
    service.update_obligation(task.obligation_id, due_at=manual_due, now=V1_NOW)
    secondary = Task(
        id="canvas:secondary",
        source="canvas",
        title=task.title,
        course=task.course,
        due_at=task.due_at,
        tags=task.tags,
    )
    service.ingest_snapshot([task, secondary], now=V1_NOW)
    assert (
        service.store.get_obligation(task.obligation_id)["due_at"]
        == manual_due.isoformat()
    )


@pytest.mark.parametrize("operation", ["sync", "manual"])
def test_concurrent_state_write_cannot_overwrite_committed_completion(
    monkeypatch, tmp_path, operation
):
    from contextlib import contextmanager
    from threading import Event, Thread
    import nexus.core.action_service as module

    service, task, actions = seed(tmp_path)
    attempting = Event()
    done = Event()
    errors = []
    original_connect = module.connect

    @contextmanager
    def traced_connect(path):
        with original_connect(path) as conn:
            conn.set_trace_callback(
                lambda sql: attempting.set() if sql == "BEGIN IMMEDIATE" else None
            )
            yield conn

    monkeypatch.setattr(module, "connect", traced_connect)

    def complete():
        try:
            actions.complete(task.obligation_id, effort_minutes=60, now=V1_NOW)
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    worker = Thread(target=complete)
    original_write = service.store.write_obligation_ingest

    def write_after_completion_attempt(**fields):
        worker.start()
        assert attempting.wait(5)
        assert not done.is_set()
        return original_write(**fields)

    monkeypatch.setattr(
        service.store, "write_obligation_ingest", write_after_completion_attempt
    )
    if operation == "sync":
        service.ingest_snapshot([task], now=V1_NOW)
    else:
        service.update_obligation(
            task.obligation_id, due_at=task.due_at + timedelta(days=1), now=V1_NOW
        )
    worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    row = service.store.get_obligation(task.obligation_id)
    assert row["status"] == "done" and row["spent_hours"] == 1
    assert len(service.store.list_effort_samples()) == 1


def test_same_title_manual_obligations_keep_independent_state(tmp_path):
    service = AcademicStateService(tmp_path)
    first = service.add_manual_obligation(
        title="Weekly reading",
        course="CS",
        due_at=V1_NOW + timedelta(days=1),
        remaining_hours=2,
        now=V1_NOW,
    )
    second = service.add_manual_obligation(
        title="Weekly reading",
        course="CS",
        due_at=V1_NOW + timedelta(days=8),
        remaining_hours=3,
        now=V1_NOW,
    )
    assert first["id"] != second["id"]
    assert service.store.get_obligation(first["id"])["remaining_hours"] == 2
    assert service.store.get_obligation(second["id"])["remaining_hours"] == 3


def test_restoring_cancelled_work_never_revives_its_old_timer(tmp_path):
    service, task, actions = seed(tmp_path)
    other = service.add_manual_obligation(title="Other work", course="CS", now=V1_NOW)
    actions.start(task.obligation_id, now=V1_NOW)
    task.calendar_status = "cancelled"
    service.ingest_snapshot([task], now=V1_NOW + timedelta(minutes=30))
    actions.start(other["id"], now=V1_NOW + timedelta(hours=1))
    actions.complete(other["id"], now=V1_NOW + timedelta(hours=2))
    task.calendar_status = "confirmed"
    service.ingest_snapshot([task], now=V1_NOW + timedelta(hours=3))
    assert actions.running_action(now=V1_NOW + timedelta(hours=3)) is None
    actions.start(task.obligation_id, now=V1_NOW + timedelta(hours=3))
    actions.complete(task.obligation_id, now=V1_NOW + timedelta(hours=4))
    sample = service.store.list_effort_samples(obligation_id=task.obligation_id)[0]
    assert sample["minutes"] == 60


def test_state_form_prefills_independent_items_and_uses_deadline_date_timezone(
    monkeypatch, tmp_path
):
    from datetime import date, time
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    service = AcademicStateService(tmp_path)
    first = service.add_manual_obligation(
        title="Reading",
        course="CS",
        due_at=V1_NOW + timedelta(days=1),
        remaining_hours=2,
        now=V1_NOW,
    )
    second = service.add_manual_obligation(
        title="Reading",
        course="CS",
        due_at=V1_NOW + timedelta(days=8),
        remaining_hours=3,
        now=V1_NOW,
    )
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    app = AppTest.from_file(
        Path(__file__).parents[1] / "src/nexus/core/dashboard_app.py"
    ).run(timeout=30)
    assert not app.exception
    app.button(key=f"task:{second['id']}").click().run()
    assert app.number_input(key=f"remaining:{second['id']}").value == 3
    app.date_input(key=f"due-date:{second['id']}").set_value(date(2026, 11, 6))
    app.time_input(key=f"due-time:{second['id']}").set_value(time(12))
    next(item for item in app.button if item.label == "保存修改").click().run()
    assert not app.exception
    row = service.store.get_obligation(second["id"])
    assert row["due_at"] == "2026-11-06T12:00:00-05:00"
    assert service.store.get_obligation(first["id"])["remaining_hours"] == 2


@pytest.mark.parametrize("availability", [False, True])
def test_past_deadline_does_not_turn_a_new_work_block_into_one_minute(tmp_path, availability):
    service, task, _ = seed(tmp_path)
    service.update_obligation(task.obligation_id, due_at=V1_NOW - timedelta(days=1), now=V1_NOW)
    if availability:
        service.add_availability_rule(weekday=0, starts_local="16:00", ends_local="18:00", timezone=V1_TIMEZONE_NAME)
    today = build_today(tmp_path, now=V1_NOW)
    assert today.recommendation.risk_band == "overdue"
    assert today.recommendation.block_minutes == 50
    assert build_context(data_dir=tmp_path, now=V1_NOW)["next_action"]["block_minutes"] == 50
