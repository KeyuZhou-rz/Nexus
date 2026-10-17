"""One task list, one current task, and a small custom-task editor."""
from __future__ import annotations

from datetime import datetime, time
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from ..academic_state import display_course, refresh_from_snapshot
from ..academic_state_service import AcademicStateService
from ..action_service import ActionService
from ..config import default_config
from ..context_api import persist_next_action
from ..models import FeedSource
from ..onboarding import connect_calendar_feed
from ..storage import load_feeds, load_tasks, tasks_last_updated
from ..sync_service import load_sync_receipt, run_sync
from ..task_service import apply_completions, load_completions
from ..today import build_today
from ..v1_config import V1_TIMEZONE_NAME
from .copy import local_due, when_copy
from .styles import DASHBOARD_CSS


TIMEZONE = ZoneInfo(V1_TIMEZONE_NAME)
OPEN_STATUSES = {"open", "in_progress", "overdue", "missing"}


def main() -> None:
    st.set_page_config(page_title="Nexus", page_icon="🧭", layout="wide")
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    data_dir = Path(default_config().data_dir)
    now = datetime.now(TIMEZONE)
    snapshot = apply_completions(load_tasks(data_dir / "tasks.json"), load_completions(data_dir))
    refresh_from_snapshot(data_dir, snapshot)
    service = AcademicStateService(data_dir)
    rows = service.store.list_obligations()
    open_rows = sorted(
        (row for row in rows if row["status"] in OPEN_STATUSES),
        key=lambda row: (row["status"] == "missing", local_due(row) or datetime.max.replace(tzinfo=TIMEZONE), row["id"]),
    )
    done_rows = [row for row in rows if row["status"] == "done"]
    today = build_today(data_dir, now=now)
    selected_id = _selected_id(open_rows, done_rows, today)
    selected = next((row for row in rows if row["id"] == selected_id), None)

    _render_header(data_dir)
    notice = st.session_state.pop("notice", None)
    if notice:
        st.caption(notice)
    if load_sync_receipt(data_dir).get("ok") is False:
        st.caption("有课程尚未更新，清单保留了上次的任务。展开「课程来源」可以重试。")
    if st.session_state.get("custom_open"):
        _render_custom_task(service, data_dir)
    with st.container(key="nexus-workspace"):
        left, right = st.columns([1.05, 1], gap="large")
        with left, st.container(key="task-list"):
            st.markdown("## 有什么任务")
            st.caption(f"{len(open_rows)} 件未完成")
            if not open_rows:
                st.caption("清单是空的。可以加上自己的任务，或接入课程日历。" if not done_rows else "这些任务都做完了。")
            for row in open_rows:
                _render_task_row(row, selected_id, today.running, now)
            if done_rows:
                with st.expander(f"已完成 · {len(done_rows)} 件"):
                    for row in done_rows:
                        _render_task_row(row, selected_id, today.running, now)
        with right, st.container(key="current-task"):
            _render_current_task(selected, today, service, data_dir, now)
    _render_sources(data_dir)


def _selected_id(open_rows, done_rows, today) -> str | None:
    if today.running:
        return today.running["obligation_id"]
    selected = st.session_state.get("selected_obligation_id")
    if any(row["id"] == selected for row in (*open_rows, *done_rows)):
        return selected
    if today.recommendation and today.recommendation.obligation_id:
        return today.recommendation.obligation_id
    return next((row["id"] for row in open_rows if row["id"] not in today.deferred_until), None)


def _render_header(data_dir: Path) -> None:
    with st.container(key="nexus-header"):
        title, updated, add = st.columns([1, 2, 1.4], vertical_alignment="center")
        title.markdown("# Nexus")
        last_updated = tasks_last_updated(data_dir / "tasks.json")
        updated.caption(
            f"清单更新于 {last_updated.astimezone(TIMEZONE):%m 月 %d 日 %H:%M}"
            if last_updated else "任务保存在这台电脑上"
        )
        if add.button("＋ 自定义任务", type="primary", use_container_width=True):
            st.session_state["custom_open"] = not st.session_state.get("custom_open", False)
            st.rerun()


def _render_task_row(row, selected_id, running, now) -> None:
    course = display_course(row.get("course")) or "自己的任务"
    title = row["title"]
    label = f"{title}\n\n{course} · {when_copy(row, now)}"
    is_running = running and running["obligation_id"] != row["id"]
    if st.button(
        label, key=f"task:{row['id']}",
        type="primary" if selected_id == row["id"] else "secondary",
        disabled=bool(is_running), use_container_width=True,
    ):
        st.session_state["selected_obligation_id"] = row["id"]
        st.rerun()


def _render_current_task(row, today, service, data_dir, now) -> None:
    if row is None:
        st.markdown('<p class="focus-kicker">现在做这件</p><h2>可以休息一下</h2>', unsafe_allow_html=True)
        st.caption(
            "稍后处理的任务仍在清单里，想继续可以直接选择。"
            if today.deferred_until else "有新的事情，随时加到清单里。"
        )
        return
    oid = row["id"]
    done = row["status"] == "done"
    running = today.running and today.running["obligation_id"] == oid
    heading = "已经完成" if done else "正在做" if running else "现在做这件"
    st.markdown(
        f'<p class="focus-kicker">{heading}</p>'
        f'<h2 class="focus-title">{escape(row["title"])}</h2>',
        unsafe_allow_html=True,
    )
    course = display_course(row.get("course")) or "自己的任务"
    st.caption(course)
    st.caption(when_copy(row, now))
    if done:
        return
    if row["status"] == "missing":
        st.caption("课程来源最近没有这条任务。确认还要做，可以直接开始；已经做完就标记完成。")
    if running:
        st.caption("先做这一件。暂停后可以换任务。")
    actions = ActionService(data_dir)
    start, complete = st.columns(2)
    if start.button("暂停" if running else "开始做", key=f"start:{oid}", type="primary"):
        try:
            if running:
                actions.stop(oid)
            else:
                actions.start(oid)
        except ValueError:
            st.session_state["notice"] = "另一件任务正在进行，请先暂停。"
        _commit_action(data_dir)
    if complete.button("完成这件", key=f"done:{oid}"):
        actions.complete(oid)
        st.session_state.pop("selected_obligation_id", None)
        st.session_state["notice"] = "已完成。下一件任务已经准备好。"
        _commit_action(data_dir)
    with st.expander("修改截止与用时"):
        _render_edit_task(row, service, data_dir, now)


def _commit_action(data_dir: Path) -> None:
    persist_next_action(data_dir=data_dir)
    st.rerun()


def _render_custom_task(service, data_dir) -> None:
    with st.container(key="custom-task"):
        heading, close = st.columns([4, 1])
        heading.markdown("## 加上一件事")
        if close.button("收起"):
            st.session_state["custom_open"] = False
            st.rerun()
        with st.form("custom_task", clear_on_submit=True):
            title = st.text_input("任务名称", placeholder="例如：准备周四的展示")
            course_col, date_col, time_col = st.columns([2, 1.3, 1])
            course = course_col.text_input("课程或分类（可选）", placeholder="自己的任务")
            due_date = date_col.date_input("截止日期（可选）", value=None, key="custom-due-date")
            due_time = time_col.time_input("截止时间", value=time(23, 59), key="custom-due-time")
            remaining = st.number_input("预计用时（小时，可选）", min_value=0.0, value=None, step=0.5)
            submitted = st.form_submit_button("加入清单", type="primary")
        if submitted:
            due = datetime.combine(due_date, due_time, tzinfo=TIMEZONE) if due_date else None
            try:
                service.add_manual_obligation(
                    title=title, course=course or None, due_at=due,
                    estimated_hours=remaining, remaining_hours=remaining,
                )
            except ValueError:
                st.error("请填写任务名称。")
            else:
                st.session_state["custom_open"] = False
                st.session_state["notice"] = "自定义任务已加入清单。"
                _commit_action(data_dir)


def _render_edit_task(row, service, data_dir, now) -> None:
    oid = row["id"]
    due = local_due(row)
    shown_due = due or now
    with st.form(f"edit:{oid}"):
        has_due = st.checkbox("设置截止时间", value=due is not None, key=f"has-due:{oid}")
        due_date = st.date_input("截止日期", value=shown_due.date(), key=f"due-date:{oid}")
        due_time = st.time_input("截止时间", value=shown_due.time(), key=f"due-time:{oid}")
        remaining = st.number_input(
            "还剩多少小时（可选）", min_value=0.0,
            value=row.get("remaining_hours"), step=0.5, key=f"remaining:{oid}",
        )
        submitted = st.form_submit_button("保存修改")
    if submitted:
        deadline = datetime.combine(due_date, due_time, tzinfo=TIMEZONE) if has_due else None
        service.update_obligation(
            oid, due_at=deadline if deadline != due else ...,
            remaining_hours=remaining if remaining != row.get("remaining_hours") else ...,
        )
        st.session_state["notice"] = "修改已保存。"
        _commit_action(data_dir)


def _google_authorized(data_dir: Path) -> bool:
    return (data_dir / "google_client_secret.json").is_file() and (data_dir / "google_token.json").is_file()


def _render_sources(data_dir: Path) -> None:
    with st.expander("课程来源"):
        if st.button("更新课程任务"):
            with st.spinner("正在更新课程任务…"):
                receipt = run_sync(data_dir=data_dir, trigger="dashboard", include_google=_google_authorized(data_dir))
            status = receipt.get("status")
            st.session_state["notice"] = (
                "课程任务已更新。" if status == "succeeded" else
                "课程正在更新，请稍后再看。" if status == "busy" else
                "这次课程更新没有完成，原来的任务已保留。"
            )
            st.rerun()
        receipt = load_sync_receipt(data_dir)
        if receipt.get("status") == "failed":
            st.caption("上次更新没有完成，可以重试。")
        feeds = load_feeds(data_dir / "feeds.json")
        for feed in feeds:
            st.caption(f"{feed.course or feed.name} · {'课程日历' if feed.kind.endswith('ical') else '课程公告'}")
        st.caption("日历提供截止时间，不含提交状态，也可能漏掉课程内容页的任务。缺少的任务可以自己加上；做完后点完成。")
        with st.form("course_source", clear_on_submit=True):
            course = st.text_input("课程名称")
            url = st.text_input("日历订阅链接", type="password", placeholder="https://...")
            submitted = st.form_submit_button("接入课程")
        if submitted:
            try:
                result = connect_calendar_feed(
                    data_dir, FeedSource(kind="brightspace_ical", name=course, course=course or None, url=url),
                    sync=run_sync,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["notice"] = (
                    "课程已接入，任务已更新。" if result["sync"].get("status") == "succeeded"
                    else "课程已保存，暂时没能更新任务，可以稍后重试。"
                )
                st.rerun()
