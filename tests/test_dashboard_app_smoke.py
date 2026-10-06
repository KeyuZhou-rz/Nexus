from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from nexus.demo import create_demo_runtime
from nexus.storage import load_feeds, load_tasks

APP_PATH = Path(__file__).parents[1] / 'src/nexus/dashboard_app.py'


def open_app(monkeypatch, data_dir):
    monkeypatch.setenv('NEXUS_DATA_DIR', str(data_dir))
    monkeypatch.setenv('NEXUS_LLM_ENABLED', '0')
    return AppTest.from_file(APP_PATH).run(timeout=30)


def click(app, label):
    return next(button for button in app.button if button.label == label).click().run()


def test_dashboard_renders_one_task_workspace(monkeypatch, tmp_path):
    data_dir = tmp_path / 'demo'
    create_demo_runtime(data_dir)
    app = open_app(monkeypatch, data_dir)
    assert not app.exception
    assert not app.tabs
    assert not app.sidebar.button
    labels = {button.label for button in app.button}
    assert {'＋ 自定义任务', '开始做', '完成这件'}.issubset(labels)
    assert '接受' not in labels and '推迟' not in labels
    assert not any(item.label == '任务名称' for item in app.text_input)
    markdown = ' '.join(item.value for item in app.markdown)
    assert '有什么任务' in markdown and '现在做这件' in markdown
    assert not any(item.label == 'Priority' for item in app.selectbox)


def test_empty_dashboard_offers_custom_entry(monkeypatch, tmp_path):
    app = open_app(monkeypatch, tmp_path)
    assert not app.exception
    assert any('清单是空的' in item.value for item in app.caption)
    click(app, '＋ 自定义任务')
    assert any(item.label == '任务名称' for item in app.text_input)


def test_dashboard_manual_task_form_writes_user_owned_task(monkeypatch, tmp_path):
    data_dir = tmp_path / 'demo'
    create_demo_runtime(data_dir)
    app = open_app(monkeypatch, data_dir)
    click(app, '＋ 自定义任务')
    next(item for item in app.text_input if item.label == '任务名称').input('Read chapter 7')
    next(item for item in app.text_input if item.label == '课程或分类（可选）').input('CS202_OS')
    click(app, '加入清单')
    manual = [task for task in load_tasks(data_dir / 'tasks.json') if task.id.startswith('manual:')]
    assert len(manual) == 1
    assert manual[0].title == 'Read chapter 7' and manual[0].course == 'CS202_OS'
    assert manual[0].due_at is None and manual[0].estimated_hours is None
    assert not app.exception
    assert any(button.label.startswith('Read chapter 7') for button in app.button)
    assert not any(item.label == '任务名称' for item in app.text_input)


def test_dashboard_sync_button_uses_shared_sync_service(monkeypatch, tmp_path):
    data_dir = tmp_path / 'demo'
    create_demo_runtime(data_dir)
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {'status': 'succeeded', 'summary': {'task_count': 3}}

    monkeypatch.setattr('nexus.dashboard.app.run_sync', fake_sync)
    app = open_app(monkeypatch, data_dir)
    # AppTest can interact with widgets inside the collapsed expander.
    click(app, '更新课程任务')
    assert calls == [{'data_dir': data_dir, 'trigger': 'dashboard', 'include_google': False}]
    assert any('已更新' in item.value for item in app.caption)


def test_dashboard_sync_includes_google_when_authorized(monkeypatch, tmp_path):
    data_dir = tmp_path / 'demo'
    create_demo_runtime(data_dir)
    (data_dir / 'google_client_secret.json').write_text('{}')
    (data_dir / 'google_token.json').write_text('{}')
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {'status': 'succeeded'}

    monkeypatch.setattr('nexus.dashboard.app.run_sync', fake_sync)
    app = open_app(monkeypatch, data_dir)
    click(app, '更新课程任务')
    assert calls == [{'data_dir': data_dir, 'trigger': 'dashboard', 'include_google': True}]


def test_dashboard_setup_syncs_feed_without_google(monkeypatch, tmp_path):
    data_dir = tmp_path / 'demo'
    create_demo_runtime(data_dir)
    calls = []

    def fake_sync(**kwargs):
        calls.append(kwargs)
        return {'status': 'succeeded', 'summary': {'task_count': 4}}

    monkeypatch.setattr('nexus.dashboard.app.run_sync', fake_sync)
    app = open_app(monkeypatch, data_dir)
    next(item for item in app.text_input if item.label == '课程名称').input('Circuits')
    next(item for item in app.text_input if item.label == '日历订阅链接').input('https://example.edu/cal.ics')
    click(app, '接入课程')
    assert not app.exception
    assert calls == [{'data_dir': data_dir, 'trigger': 'setup', 'include_google': False}]
    assert any(feed.course == 'Circuits' for feed in load_feeds(data_dir / 'feeds.json'))
    assert any('课程已接入' in item.value for item in app.caption)


def test_custom_deadline_uses_its_date_timezone_and_requires_only_a_title(monkeypatch, tmp_path):
    from datetime import date, time
    app = open_app(monkeypatch, tmp_path)
    click(app, '＋ 自定义任务')
    next(item for item in app.text_input if item.label == '任务名称').input('November presentation')
    app.date_input(key='custom-due-date').set_value(date(2026, 11, 6))
    app.time_input(key='custom-due-time').set_value(time(12))
    click(app, '加入清单')
    assert not app.exception
    task = load_tasks(tmp_path / 'tasks.json')[0]
    assert task.due_at.isoformat() == '2026-11-06T12:00:00-05:00'
    assert task.course is None and task.estimated_hours is None


def test_blank_custom_title_does_not_create_an_obligation(monkeypatch, tmp_path):
    from nexus.academic_state_service import AcademicStateService
    app = open_app(monkeypatch, tmp_path)
    click(app, '＋ 自定义任务')
    click(app, '加入清单')
    assert not app.exception
    assert app.error
    assert not AcademicStateService(tmp_path).store.list_obligations()
