"""Task selection and feedback stay attached to canonical obligation IDs."""
from datetime import datetime, timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from nexus.core.academic_state_service import AcademicStateService
from nexus.core.action_service import ActionService
from nexus.core.today import build_today
from nexus.core.v1_config import V1_NOW, V1_TZ

APP_PATH = Path(__file__).parents[1] / 'src/nexus/core/dashboard_app.py'


def seeded_app(monkeypatch, tmp_path):
    state = AcademicStateService(tmp_path)
    # The page renders against the real clock, so seed deadlines relative to it.
    now = datetime.now(V1_TZ).replace(second=0, microsecond=0)
    first = state.add_manual_obligation(title='Reading', course='CS', due_at=now + timedelta(days=1), remaining_hours=2)
    second = state.add_manual_obligation(title='Reading', course='Math', due_at=now + timedelta(days=8), remaining_hours=3)
    monkeypatch.setenv('NEXUS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('NEXUS_LLM_ENABLED', '0')
    return state, first, second, AppTest.from_file(APP_PATH).run(timeout=30)


def test_dashboard_defaults_to_the_engine_recommendation(monkeypatch, tmp_path):
    state, first, second, app = seeded_app(monkeypatch, tmp_path)
    assert not app.exception
    rec = build_today(tmp_path).recommendation
    assert app.button(key=f'start:{rec.obligation_id}')
    assert not app.tabs
    assert any('有什么任务' in item.value for item in app.markdown)
    assert len([b for b in app.button if b.key and b.key.startswith('task:')]) == 2
    assert 'CS' in app.button(key=f'task:{first["id"]}').label
    assert 'Math' in app.button(key=f'task:{second["id"]}').label


def test_selected_task_starts_and_refresh_keeps_the_running_task(monkeypatch, tmp_path):
    state, first, second, app = seeded_app(monkeypatch, tmp_path)
    app.button(key=f'task:{second["id"]}').click().run()
    app.button(key=f'start:{second["id"]}').click().run()
    assert not app.exception
    assert state.store.get_obligation(second['id'])['status'] == 'in_progress'
    assert state.store.get_obligation(first['id'])['status'] == 'open'
    assert app.button(key=f'task:{first["id"]}').disabled
    restarted = AppTest.from_file(APP_PATH).run(timeout=30)
    assert restarted.button(key=f'start:{second["id"]}').label == '暂停'
    restarted.button(key=f'start:{second["id"]}').click().run()
    assert ActionService(tmp_path).running_action() is None
    assert not restarted.button(key=f'task:{first["id"]}').disabled


def test_completion_selects_another_task_and_survives_refresh(monkeypatch, tmp_path):
    state, first, second, app = seeded_app(monkeypatch, tmp_path)
    app.button(key=f'task:{second["id"]}').click().run()
    app.button(key=f'done:{second["id"]}').click().run()
    assert not app.exception
    assert state.store.get_obligation(second['id'])['status'] == 'done'
    assert app.button(key=f'start:{first["id"]}')
    assert any(exp.label == '已完成 · 1 件' for exp in app.expander)
    restarted = AppTest.from_file(APP_PATH).run(timeout=30)
    assert restarted.button(key=f'start:{first["id"]}')
    assert state.store.get_obligation(second['id'])['status'] == 'done'
    restarted.button(key=f'done:{first["id"]}').click().run()
    assert not restarted.exception
    assert not any(b.label == '开始做' for b in restarted.button)
    assert any('可以休息一下' in m.value for m in restarted.markdown)
    assert not state.store.list_effort_samples()  # Completion without timing does not invent effort.


def test_completed_selection_cannot_be_started_again(monkeypatch, tmp_path):
    state, first, second, app = seeded_app(monkeypatch, tmp_path)
    app.button(key=f'task:{second["id"]}').click().run()
    ActionService(tmp_path).complete(second['id'])
    app.run()
    assert not app.exception
    # A completed selection can still be inspected, but cannot be started again.
    assert not any(b.key == f'start:{second["id"]}' for b in app.button)
    app.button(key=f'task:{first["id"]}').click().run()
    assert app.button(key=f'start:{first["id"]}')


def test_missing_source_task_stays_visible_and_can_be_confirmed_by_starting(monkeypatch, tmp_path):
    from nexus.core.models import Task
    state = AcademicStateService(tmp_path)
    task = Task(id='school:one', title='Course assignment', source='brightspace', due_at=V1_NOW + timedelta(days=1), tags=['assignment'])
    state.ingest_snapshot([task], source='brightspace', now=V1_NOW)
    state.ingest_snapshot([], source='brightspace', reconcile_missing=True, now=V1_NOW + timedelta(hours=1))
    monkeypatch.setenv('NEXUS_DATA_DIR', str(tmp_path))
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert state.store.get_obligation(task.obligation_id)['status'] == 'missing'
    assert any('来源待核对' in button.label for button in app.button)
    app.button(key=f'start:{task.obligation_id}').click().run()
    assert not app.exception
    assert state.store.get_obligation(task.obligation_id)['status'] == 'in_progress'


def test_editing_without_changes_preserves_source_deadline_and_remaining_override(monkeypatch, tmp_path):
    state, first, second, app = seeded_app(monkeypatch, tmp_path)
    app.button(key=f'task:{second["id"]}').click().run()
    before = state.store.get_obligation(second['id'])
    next(b for b in app.button if b.label == '保存修改').click().run()
    after = state.store.get_obligation(second['id'])
    assert not app.exception
    assert before['due_at'] == after['due_at']
    assert before['remaining_override_hours'] == after['remaining_override_hours']


def test_legacy_deferred_tasks_remain_selectable_without_an_automatic_restart(monkeypatch, tmp_path):
    state, first, second, app = seeded_app(monkeypatch, tmp_path)
    actions = ActionService(tmp_path)
    actions.defer(first['id'], reason='Later')
    actions.defer(second['id'], reason='Later')
    app.run()
    assert not app.exception
    assert not any(b.label == '开始做' for b in app.button)
    assert app.button(key=f'task:{first["id"]}')
    app.button(key=f'task:{first["id"]}').click().run()
    app.button(key=f'start:{first["id"]}').click().run()
    assert actions.running_action()['obligation_id'] == first['id']


def test_stale_overdue_work_is_not_auto_selected(monkeypatch, tmp_path):
    state = AcademicStateService(tmp_path)
    now = datetime.now(V1_TZ).replace(second=0, microsecond=0)
    stale = state.add_manual_obligation(title='Old essay', due_at=now - timedelta(days=3), remaining_hours=1)
    monkeypatch.setenv('NEXUS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('NEXUS_LLM_ENABLED', '0')
    app = AppTest.from_file(APP_PATH).run(timeout=30)
    assert not app.exception
    assert app.button(key=f'task:{stale["id"]}')
    assert not any(b.key == f'start:{stale["id"]}' for b in app.button)
    assert any('已过截止超过一天' in item.value for item in app.caption)
