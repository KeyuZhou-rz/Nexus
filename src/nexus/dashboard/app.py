from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from ..config import default_config
from ..context_api import build_context, persist_next_action
from ..models import FeedSource
from ..onboarding import connect_calendar_feed
from ..storage import load_feeds, load_tasks, tasks_last_updated

from ..academic_state import (
    display_course,
    obligation_id_for_task,
    refresh_from_snapshot,
    tasks_from_obligations,
)
from ..academic_state_service import AcademicStateService
from ..action_service import ActionService
from ..changes import acknowledge_change, change_key, list_inbox_changes
from ..course_state import build_course_states
from ..intelligence.workload import build_judgment, resolve_effort
from ..today import build_today
from ..utils import localize_dt
from ..sync_service import load_sync_receipt, run_sync
from ..task_service import (
    add_manual_task,
    apply_completions,
    completed_task_ids,
    load_completions,
    mark_task_done,
    start_task,
)
from ..v1_config import V1_TIMEZONE_NAME
from .styles import DASHBOARD_CSS
from .view_model import (
    build_dashboard_state,
    load_briefing_payload,
    load_pipeline_report,
    render_task_card,
)


_WEEKDAY_CHOICES = (
    "—",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def main() -> None:
    st.set_page_config(page_title="Nexus", page_icon="🧭", layout="wide")
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    config = default_config()
    data_dir = config.data_dir
    briefing_path = data_dir / "briefing.json"
    report_path = data_dir / "pipeline_report.json"

    completions = load_completions(data_dir)
    snapshot = apply_completions(load_tasks(), completions)
    try:
        tasks = refresh_from_snapshot(data_dir, snapshot)
    except Exception:
        tasks = tasks_from_obligations(data_dir, snapshot)
    briefing_payload = load_briefing_payload(briefing_path)
    pipeline_report = load_pipeline_report(report_path)
    now_local = datetime.now().astimezone()
    context = build_context(data_dir=data_dir, now=now_local)
    sync_sources = context.get("sync", {}).get("sources", {})
    sync_receipt = load_sync_receipt(data_dir)
    learning_context = context.get("learning", {})
    today = build_today(data_dir, now=now_local)
    judgment = build_judgment(
        tasks,
        done_ids=completed_task_ids(completions),
        now=now_local,
        learning_focus=learning_context.get("focus") if isinstance(learning_context.get("focus"), list) else None,
        completions=completions,
    )
    changes = list_inbox_changes(data_dir, limit=5, tasks=snapshot)
    courses = build_course_states(tasks, completions=completions, now=now_local)
    rec = today.recommendation
    rec_task_id = _primary_task_id(tasks, data_dir, rec.obligation_id) if rec and rec.obligation_id else None
    state = build_dashboard_state(
        tasks,
        briefing_payload=briefing_payload,
        pipeline_report=pipeline_report,
        sync_sources=sync_sources,
        sync_receipt=sync_receipt,
        learning_context=learning_context,
        generated_at=now_local,
        last_updated=tasks_last_updated(),
        anchor_task_id=rec_task_id or judgment.anchor_task_id,
    )

    _render_sidebar(data_dir=data_dir, state=state, include_google=_google_authorized(data_dir))

    tab_today, tab_state, tab_setup = st.tabs(["Today", "State", "Setup"])

    with tab_today:
        _render_today(today, tasks, data_dir, state, changes, now_local)

    with tab_state:
        _render_state(
            state,
            data_dir=data_dir,
            tasks=tasks,
            today=today,
            courses=courses,
            now_local=now_local,
        )

    with tab_setup:
        _render_setup(data_dir, state)


def _google_authorized(data_dir: Path) -> bool:
    root = Path(data_dir).expanduser()
    return (root / "google_client_secret.json").is_file() and (root / "google_token.json").is_file()


def _project_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _google_authorized_elsewhere(data_dir: Path) -> bool:
    project = _project_data_dir()
    if project.resolve() == Path(data_dir).expanduser().resolve():
        return False
    return _google_authorized(project)


def _render_sidebar(*, data_dir: Path, state, include_google: bool) -> None:
    with st.sidebar:
        st.markdown("### Nexus")
        st.caption(state.last_updated_text)
        if st.button("Sync", type="primary", use_container_width=True):
            with st.spinner("Updating…"):
                receipt = run_sync(
                    data_dir=data_dir,
                    trigger="dashboard",
                    include_google=include_google,
                )
            google = (receipt.get("sources") or {}).get("google") if isinstance(receipt.get("sources"), dict) else {}
            st.session_state["sync_notice"] = {
                "status": receipt.get("status"),
                "pulled_google": bool(include_google),
                "google_status": google.get("status") if isinstance(google, dict) else None,
            }
            st.rerun()
        notice = st.session_state.pop("sync_notice", None)
        if isinstance(notice, str):
            notice = {"status": notice}
        if isinstance(notice, dict):
            status = notice.get("status")
            if status == "succeeded":
                if notice.get("pulled_google"):
                    st.caption("已更新，含 Gmail / Calendar。")
                elif _google_authorized_elsewhere(data_dir):
                    st.caption("已更新，但没拉 Gmail：当前是练习目录，授权在 data/。用 nexus dashboard 打开 data/ 再 Sync。")
                else:
                    st.caption("已更新，没拉 Gmail（当前目录没有 Google 授权）。")
            elif status == "busy":
                st.caption("正在同步。")
            elif status:
                st.caption("同步未完成，打开 Setup 查看来源。")


def _render_today(today, tasks, data_dir, state, changes, now_local: datetime) -> None:
    rec = today.recommendation
    freshness = today.freshness or (
        state.generated_at.isoformat() if getattr(state, "generated_at", None) else ""
    )
    empty = rec is None or not rec.obligation_id
    if empty:
        missing = (None if rec is None else rec.missing_input) or today.missing_input
        empty_body = ["<p>还没有作业或考试截止。</p>"]
        if missing:
            empty_body.append(f"<p>{_html(missing)}</p>")
        empty_body.append(
            '<p class="briefing-muted">到 Setup 贴上课程日历，或到 State 用 Add task 加上一条。</p>'
        )
        st.markdown(
            f'<div class="briefing"><p class="briefing-kicker">Today · {_html(freshness)}</p>'
            f'{"".join(empty_body)}</div>',
            unsafe_allow_html=True,
        )
        if missing:
            st.caption("到 State 或 Setup 补全课程、截止、剩余时间和可学习时段。")
        _render_change_decisions(changes, data_dir)
        return

    assessment = next(
        (item for item in today.assessments if item.obligation_id == rec.obligation_id),
        None,
    )
    due = None if assessment is None else getattr(assessment, "due_at", None)
    deadline = _deadline_copy(due, now_local)
    reason_text = " · ".join(str(item) for item in (rec.reasons or ()) if item)
    hours_text = _hours_copy(rec.remaining_hours, rec.available_hours)
    blocks = [
        f'<p class="briefing-kicker">Today · {_html(freshness)}</p>',
        f'<p class="headline">{_html(rec.title or "Next action")}</p>',
    ]
    if deadline:
        blocks.append(f"<p>{_html(deadline)}</p>")
    if rec.first_step:
        blocks.append(f'<p class="suggestion">{_html(rec.first_step)}</p>')
    meta_parts = [rec.risk_band, reason_text, hours_text]
    meta = " · ".join(part for part in meta_parts if part)
    if meta:
        blocks.append(f"<p>{_html(meta)}</p>")
    if rec.explanation and rec.explanation not in (rec.first_step, hours_text, reason_text):
        blocks.append(f'<p class="briefing-muted">{_html(rec.explanation)}</p>')
    st.markdown(
        f'<div class="briefing week-block recommended">{"".join(blocks)}</div>',
        unsafe_allow_html=True,
    )
    if rec.missing_input or today.missing_input:
        st.markdown(rec.missing_input or today.missing_input)
        st.caption("到 State 或 Setup 补全课程、截止、剩余时间和可学习时段。")
    _render_today_actions(today, rec, tasks, data_dir)
    _render_change_decisions(changes, data_dir)


def _hours_copy(remaining: float | None, available: float | None) -> str:
    parts: list[str] = []
    if remaining is not None:
        parts.append(f"{remaining:g}h remaining")
    if available is not None:
        parts.append(f"{available:g}h available")
    return " · ".join(parts)


def _deadline_copy(due, now_local: datetime) -> str:
    if due is None:
        return ""
    local = localize_dt(due, now_local) or due
    if local.tzinfo is None and now_local.tzinfo is not None:
        local = local.replace(tzinfo=now_local.tzinfo)
    if local < now_local:
        return "已截止"
    days = (local.date() - now_local.date()).days
    if days == 0:
        return "今天截止"
    if days == 1:
        return "明天截止"
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return f"{weekdays[local.weekday()]}截止"


def _primary_task_id(tasks, data_dir: Path, obligation_id: str | None) -> str | None:
    if not obligation_id:
        return None
    for task in tasks:
        if obligation_id_for_task(task) == obligation_id:
            return task.id
    row = AcademicStateService(data_dir).store.get_obligation(obligation_id)
    if row is None:
        return None
    return row.get("primary_task_id")


def _obligation_accepted(data_dir: Path, obligation_id: str) -> bool:
    events = ActionService(data_dir).store.list_action_events(
        obligation_id=obligation_id, limit=50
    )
    return any(item.get("event_type") == "accepted" for item in events)


def _commit_action(data_dir: Path) -> None:
    persist_next_action(data_dir=data_dir)
    st.rerun()


def _render_today_actions(today, rec, tasks, data_dir: Path) -> None:
    oid = rec.obligation_id
    running = today.running if isinstance(today.running, dict) else None
    running_oid = None if running is None else str(running.get("obligation_id") or "")
    this_running = running_oid == oid
    accepted = _obligation_accepted(data_dir, oid)
    if this_running:
        elapsed = running.get("elapsed_minutes")
        minutes = 0 if elapsed is None else int(elapsed)
        st.caption(f"计时中 · {minutes} 分钟")
    elif accepted:
        st.caption("已接受。计时请点开始做。")
    cols = st.columns(3)
    with cols[0]:
        if st.button("接受", disabled=accepted, key=f"accept_{oid}"):
            ActionService(data_dir).accept(
                oid,
                block_minutes=rec.block_minutes,
                recommendation_key=rec.recommendation_key,
            )
            _commit_action(data_dir)
    with cols[1]:
        start_disabled = this_running or rec.action == "continue"
        if st.button("开始做", disabled=start_disabled, key=f"start_{oid}"):
            try:
                ActionService(data_dir).start(
                    oid, recommendation_key=rec.recommendation_key
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                _commit_action(data_dir)
    with cols[2]:
        if st.button("完成", key=f"done_{oid}", type="primary"):
            ActionService(data_dir).complete(oid)
            _commit_action(data_dir)


def _render_change_decisions(changes, data_dir: Path) -> None:
    pending = [item for item in changes if item.needs_decision]
    if not pending:
        return
    st.markdown('<div class="section-title">需要决定</div>', unsafe_allow_html=True)
    st.caption("改期、消失或有学业信号的邮件。点知道了表示你已经看过。")
    for item in pending:
        cols = st.columns([4, 1])
        cols[0].markdown(item.text)
        if cols[1].button("知道了", key=f"ack_{change_key(item)}"):
            acknowledge_change(data_dir, change_key(item))
            st.rerun()


def _open_obligations(tasks) -> list:
    return [
        task
        for task in tasks
        if getattr(task, "status", "open") != "done"
        and "work_block" not in {str(tag).lower() for tag in (task.tags or [])}
    ]


def _render_obligation_blocks(today, tasks, data_dir, now_local: datetime) -> None:
    open_tasks = _open_obligations(tasks)
    if not open_tasks:
        st.caption("没有未完成的义务。用上面的 Add task 加上作业或考试。")
        return

    rec = today.recommendation if today is not None else None
    recommended_oid = None if rec is None else rec.obligation_id
    running = today.running if today is not None and isinstance(today.running, dict) else None
    running_oid = None if running is None else str(running.get("obligation_id") or "")
    horizon = now_local + timedelta(days=7)
    this_week: list = []
    later: list = []
    undated: list = []
    for task in open_tasks:
        due = localize_dt(task.due_at, now_local)
        if due is None:
            undated.append(task)
        elif due <= horizon:
            this_week.append(task)
        else:
            later.append(task)

    def sort_key(task):
        oid = obligation_id_for_task(task)
        recommended = 0 if recommended_oid and oid == recommended_oid else 1
        due = localize_dt(task.due_at, now_local)
        stamp = due.timestamp() if due is not None else float("inf")
        return (recommended, stamp)

    this_week.sort(key=sort_key)
    later.sort(key=sort_key)
    completions = load_completions(data_dir)
    if running_oid:
        st.caption("计时绑在你点开始做的那一条上。做完点同一条的完成。")
    else:
        st.caption("和 Today 是同一份未完成义务。每条都可以开始做。")

    if this_week:
        st.markdown('<div class="section-title">这周你还有：</div>', unsafe_allow_html=True)
        for task in this_week:
            _render_task_block(
                task, recommended_oid, running_oid, rec, completions, data_dir, now_local
            )
    if later:
        st.markdown('<div class="section-title">更晚：</div>', unsafe_allow_html=True)
        for task in later:
            _render_task_block(
                task, recommended_oid, running_oid, rec, completions, data_dir, now_local
            )
    if undated:
        st.markdown('<div class="section-title">未定期：</div>', unsafe_allow_html=True)
        for task in undated:
            _render_task_block(
                task, recommended_oid, running_oid, rec, completions, data_dir, now_local
            )


def _render_task_block(
    task, recommended_oid, running_oid, rec, completions, data_dir, now_local
) -> None:
    entry = completions.get(task.id) if isinstance(completions.get(task.id), dict) else {}
    oid = obligation_id_for_task(task)
    started = bool(entry.get("started_at") and not entry.get("done_at")) or (
        bool(oid) and oid == running_oid
    )
    other_running = bool(running_oid and oid and oid != running_oid)
    elapsed = _elapsed_minutes(entry) if started else None
    recommended = bool(oid) and oid == recommended_oid
    hours, _basis = resolve_effort(task, completions)
    remaining = getattr(task, "remaining_hours", None)
    spent = getattr(task, "spent_hours", None)
    estimated = getattr(task, "estimated_hours", None)
    kind = "week-block recommended" if recommended else "week-block"
    badge = "建议" if recommended else ""
    meta_parts = [_block_when(task, now_local)]
    course = display_course(task.course)
    if course:
        meta_parts.append(course)
    if remaining is not None:
        meta_parts.append(f"剩 {remaining:g}h")
    elif estimated is not None:
        meta_parts.append(f"预计 {estimated:g}h")
    elif hours:
        meta_parts.append(f"预计 {hours:g}h")
    if spent:
        meta_parts.append(f"已用 {spent:g}h")
    if started and elapsed is not None:
        meta_parts.append(f"计时中 · {elapsed} 分钟")
    meta = " · ".join(part for part in meta_parts if part)
    badge_html = f'<div class="week-block-badge">{_html(badge)}</div>' if badge else ""
    meta_html = f'<div class="week-block-meta">{_html(meta)}</div>' if meta else ""
    st.markdown(
        f'<div class="{kind}">'
        f'<div class="week-block-head">'
        f'<div class="week-block-title">{_html(task.title)}</div>'
        f"{badge_html}"
        "</div>"
        f"{meta_html}"
        "</div>",
        unsafe_allow_html=True,
    )
    oid = obligation_id_for_task(task) or task.id
    action_cols = st.columns(2)
    with action_cols[0]:
        if st.button(
            "开始做",
            key=f"start_state_{oid}",
            disabled=started or other_running,
        ):
            if _start_obligation(data_dir, task, rec):
                _commit_action(data_dir)
    with action_cols[1]:
        if st.button(
            "完成",
            key=f"done_state_{oid}",
            type="primary" if recommended or started else "secondary",
        ):
            _complete_obligation(data_dir, task)
            _commit_action(data_dir)


def _block_when(task, now_local: datetime) -> str:
    due = localize_dt(task.due_at, now_local)
    if due is None:
        return "未设截止"
    if due < now_local:
        return "已过期"
    days = (due.date() - now_local.date()).days
    if days == 0:
        return "今天"
    if days == 1:
        return "明天"
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return weekdays[due.weekday()]


def _start_obligation(data_dir: Path, task, rec) -> bool:
    oid = obligation_id_for_task(task)
    if oid:
        key = None if rec is None else rec.recommendation_key
        try:
            ActionService(data_dir).start(oid, recommendation_key=key)
        except ValueError as exc:
            st.error(str(exc))
            return False
        return True
    start_task(data_dir, task.id)
    return True


def _complete_obligation(data_dir: Path, task) -> None:
    oid = obligation_id_for_task(task)
    if oid:
        ActionService(data_dir).complete(oid)
        return
    mark_task_done(data_dir, task.id)


def _elapsed_minutes(entry: dict) -> int | None:
    raw = entry.get("started_at")
    if not raw:
        return None
    try:
        started = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    now = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
    minutes = int((now - started).total_seconds() // 60)
    return max(0, minutes)


def _render_course_states(courses) -> None:
    if not courses:
        return
    st.markdown('<div class="section-title">课程状态</div>', unsafe_allow_html=True)
    for course in courses[:4]:
        due = "无截止"
        if course.next_due_at is not None:
            due = course.next_title or "下一件"
        risk = "有逾期" if course.risk_band == "overdue" else f"剩 {course.remaining_hours:g}h"
        label = display_course(course.course) or course.course
        pace_html = f'<div class="week-block-meta">{_html(course.pace)}</div>' if course.pace else ""
        st.markdown(
            f'<div class="week-block">'
            f'<div class="week-block-title">{_html(label)}</div>'
            f'<div class="week-block-meta">{course.open_count} 件 · {_html(risk)} · {_html(due)}</div>'
            f"{pace_html}"
            "</div>",
            unsafe_allow_html=True,
        )


def _render_schedule(state) -> None:
    st.markdown('<div class="section-title">本周日程</div>', unsafe_allow_html=True)
    groups = []
    for group in state.upcoming_groups:
        items = [item for item in group.items if "work_block" not in item.tags]
        if items:
            groups.append((group.label, items))
    if not groups:
        st.caption("没有带来截止日的日程。同步课程日历后会按过期 / 今天 / 明天分组显示。")
        return
    for label, items in groups:
        st.markdown(f'<div class="group-label">{label}</div>', unsafe_allow_html=True)
        for item in items[:8]:
            st.markdown(render_task_card(item), unsafe_allow_html=True)


def _html(value: str | None) -> str:
    from html import escape

    return escape(value or "")


def _render_add_task(data_dir: Path, *, form_key: str) -> None:
    with st.expander("Add task", expanded=False):
        with st.form(form_key, clear_on_submit=True):
            title = st.text_input("Title", placeholder="Review chapter 4")
            course = st.text_input("Course", placeholder="CS202_OS")
            remaining = st.number_input(
                "Remaining hours", min_value=0.0, value=0.0, step=0.5
            )
            has_deadline = st.checkbox("Set a deadline", value=True)
            due_date = None
            due_time = None
            if has_deadline:
                date_col, time_col = st.columns(2)
                due_date = date_col.date_input("Due date")
                due_time = time_col.time_input("Due time", value=time(23, 59))
            tags = st.text_input("Tags", placeholder="reading, exam")
            notes = st.text_area("Notes", placeholder="Optional context")
            st.caption("Optional study windows. Weekday 0 = Monday.")
            windows = []
            for index in (1, 2):
                weekday_col, start_col, end_col = st.columns(3)
                weekday_label = weekday_col.selectbox(
                    f"Weekday {index}",
                    options=list(_WEEKDAY_CHOICES),
                    index=0,
                )
                start_default = time(14, 0) if index == 1 else time(16, 0)
                start_at = start_col.time_input(f"Start {index}", value=start_default)
                end_at = end_col.time_input(f"End {index}", value=time(18, 0))
                weekday = (
                    None
                    if weekday_label == "—"
                    else _WEEKDAY_CHOICES.index(weekday_label) - 1
                )
                windows.append((weekday, start_at, end_at))
            submitted = st.form_submit_button("Add task", type="primary")

        if submitted:
            deadline = (
                datetime.combine(due_date, due_time).astimezone()
                if has_deadline and due_date is not None and due_time is not None
                else None
            )
            remaining_hours = float(remaining) if remaining else None
            tag_list = [part.strip() for part in tags.split(",") if part.strip()]
            try:
                service = AcademicStateService(data_dir)
                service.add_manual_obligation(
                    title=title,
                    course=course or None,
                    due_at=deadline,
                    estimated_hours=remaining_hours,
                    remaining_hours=remaining_hours,
                    tags=tag_list,
                )
                add_manual_task(
                    data_dir,
                    title=title,
                    due_at=deadline,
                    course=course,
                    priority=1,
                    tags=tag_list,
                    notes=notes,
                )
                for weekday, start_at, end_at in windows:
                    if weekday is None or start_at is None or end_at is None:
                        continue
                    if end_at <= start_at:
                        continue
                    service.add_availability_rule(
                        weekday=int(weekday),
                        starts_local=start_at.strftime("%H:%M"),
                        ends_local=end_at.strftime("%H:%M"),
                        timezone=V1_TIMEZONE_NAME,
                    )
            except ValueError as exc:
                st.error(str(exc))
            else:
                persist_next_action(data_dir=data_dir)
                st.success("已加入义务，Today 会更新。")
                st.rerun()


def _render_state(state, *, data_dir: Path, tasks, today, courses, now_local: datetime) -> None:
    st.caption("和 Today 同一份未完成义务。完成请在 Today 点。")
    _render_add_task(data_dir, form_key="manual_task")
    _render_obligation_blocks(today, tasks, data_dir, now_local)
    _render_course_states(courses)
    _render_schedule(state)
    _render_tasks_table(state)


def _render_tasks_table(state) -> None:
    filter_cols = st.columns(4)
    course = filter_cols[0].selectbox("Course", ["All"] + state.available_courses, index=0)
    source = filter_cols[1].selectbox("Source", ["All"] + state.available_sources, index=0)
    status = filter_cols[2].selectbox("Status", ["open", "done", "All"], index=0)
    tag = filter_cols[3].selectbox("Tag", ["All"] + state.available_tags, index=0)

    rows = [
        row for row in state.tasks_table
        if "work_block" not in {part.strip() for part in row["tags"].split(",") if part.strip()}
    ]
    if course != "All":
        rows = [row for row in rows if row["course"] == course]
    if source != "All":
        rows = [row for row in rows if row["source"] == source]
    if status != "All":
        rows = [row for row in rows if row["status"] == status]
    if tag != "All":
        rows = [row for row in rows if tag in {part.strip() for part in row["tags"].split(",") if part.strip()}]

    if status == "open":
        st.caption(f"{len(rows)} 件未完成")
    elif status == "done":
        st.caption(f"{len(rows)} 件已完成")
    else:
        st.caption(f"{len(rows)} 件")
    if not rows:
        st.info("没有未完成的任务。" if status == "open" else "没有符合条件的任务。")
        return

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


def _render_status(state, *, briefing_path: Path, report_path: Path) -> None:
    status_col, file_col = st.columns([1, 1], gap="large")

    with status_col:
        st.markdown('<div class="section-title">Pipeline</div>', unsafe_allow_html=True)
        if state.pipeline_ok is True:
            st.success(state.pipeline_summary)
        elif state.pipeline_ok is False:
            st.error(state.pipeline_summary)
        else:
            st.info(state.pipeline_summary)

        if state.pipeline_steps:
            for step in state.pipeline_steps:
                label = str(step.get("name", "unknown"))
                message = str(step.get("message", ""))
                if step.get("ok") is True:
                    st.write(f"✅ {label}: {message}")
                else:
                    st.write(f"⚠️ {label}: {message}")
        else:
            st.caption("No step-level pipeline report available.")

        st.markdown('<div class="section-title">Sources</div>', unsafe_allow_html=True)
        _render_sync_table(state)

        st.markdown('<div class="section-title">Learning memory</div>', unsafe_allow_html=True)
        _render_learning_summary(state, show_evidence=True)

    with file_col:
        st.markdown('<div class="section-title">Last sync</div>', unsafe_allow_html=True)
        _render_sync_receipt(state.sync_receipt)

        st.markdown('<div class="section-title">Files</div>', unsafe_allow_html=True)
        st.write(f"Briefing path: `{briefing_path}`")
        st.write(f"Report path: `{report_path}`")
        st.write(f"Snapshot time: `{state.generated_at.isoformat()}`")


def _render_sync_receipt(receipt: dict[str, object]) -> None:
    if not receipt:
        st.caption("No sync receipt available yet.")
        return
    summary = receipt.get("summary") if isinstance(receipt.get("summary"), dict) else {}
    status = str(receipt.get("status") or "unknown").replace("_", " ").title()
    st.write(f"Status: **{status}**")
    st.write(f"Trigger: `{receipt.get('trigger') or 'unknown'}`")
    st.write(f"Duration: `{receipt.get('duration_seconds', 0)}s`")
    metric_cols = st.columns(2)
    metric_cols[0].metric("Collected tasks", summary.get("task_count", 0))
    metric_cols[1].metric("Linked weak points", summary.get("linked_focus_count", 0))
    st.caption(f"Finished: {receipt.get('finished_at') or '-'}")


def _sync_label(source: dict[str, object]) -> str:
    status = str(source.get("status") or "unknown").replace("_", " ").title()
    freshness = str(source.get("freshness") or "unknown").replace("_", " ").title()
    return f"{status} · {freshness}"


def _render_sync_summary(state) -> None:
    if not state.sync_sources:
        return
    st.markdown('<div class="section-title">Sync</div>', unsafe_allow_html=True)
    columns = st.columns(len(state.sync_sources))
    for column, source_name in zip(columns, sorted(state.sync_sources)):
        source = state.sync_sources[source_name]
        with column:
            status = str(source.get("status") or "unknown")
            if status == "failed":
                st.error(f"{source_name.title()}\n\n{_sync_label(source)}")
            elif status == "succeeded":
                st.success(f"{source_name.title()}\n\n{_sync_label(source)}")
            else:
                st.info(f"{source_name.title()}\n\n{_sync_label(source)}")


def _render_sync_table(state) -> None:
    if not state.sync_sources:
        st.caption("No source status available yet.")
        return
    rows = []
    for name in sorted(state.sync_sources):
        source = state.sync_sources[name]
        rows.append({
            "Source": name,
            "Status": source.get("status", "unknown"),
            "Configured": "Yes" if source.get("configured") else "No",
            "Freshness": source.get("freshness", "unknown"),
            "Items": source.get("item_count", 0),
            "Last success": source.get("last_success_at") or "-",
            "Error": source.get("error") or "",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_learning_summary(state, *, show_evidence: bool = False) -> None:
    learning = state.learning_context
    if not learning or not learning.get("available"):
        reason = str(learning.get("reason") or "not initialized").replace("_", " ")
        st.caption(f"Learning memory: {reason}.")
        return

    counts = learning.get("counts") if isinstance(learning.get("counts"), dict) else {}
    metric_cols = st.columns(4)
    metric_cols[0].metric("Concepts", counts.get("total_concepts", 0))
    metric_cols[1].metric("Mastered", counts.get("mastered", 0))
    metric_cols[2].metric("Review queue", len(learning.get("review_queue") or []))
    metric_cols[3].metric("Critical", counts.get("critical", 0))
    weak_points = learning.get("weak_points") or []
    if weak_points:
        st.caption("Weak points: " + ", ".join(str(item) for item in weak_points[:5]))
    evidence = learning.get("evidence") or []
    if show_evidence and evidence:
        rows = []
        for item in evidence[:20]:
            rows.append({
                "Concept": item.get("concept_name") or item.get("concept_id"),
                "Mastery": item.get("current_mastery"),
                "Event": item.get("event_type"),
                "Source": item.get("source"),
                "Trust": item.get("trust_status"),
                "Confidence": item.get("confidence"),
                "Time": item.get("timestamp") or "-",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_learning_focus(state) -> None:
    st.markdown('<div class="section-title">依据 · 弱项</div>', unsafe_allow_html=True)
    learning = state.learning_context
    if not learning or not learning.get("available"):
        st.info("Learning memory is not initialized yet.")
        return
    focus = learning.get("focus") or []
    if not focus:
        st.info("No evidence-backed weak concepts yet.")
        return
    for item in focus[:5]:
        if isinstance(item, dict):
            st.markdown(render_learning_focus_card(item), unsafe_allow_html=True)


def _render_setup(data_dir: Path, state) -> None:
    notice = st.session_state.pop("setup_notice", None)
    if isinstance(notice, dict):
        kind = notice.get("kind")
        text = str(notice.get("text") or "")
        if kind == "success":
            st.success(text)
        elif kind == "warning":
            st.warning(text)
        else:
            st.error(text)

    st.markdown("### 已接来源")
    st.caption(f"数据目录：`{data_dir}`。侧栏 Sync 会拉已授权的 Gmail / Calendar，以及已保存的 Brightspace 日历。")
    _render_sync_table(state)
    google_ready = _google_authorized(data_dir)
    if google_ready:
        st.caption("Gmail / Calendar 已授权。点 Sync 会一并更新。")
    elif _google_authorized_elsewhere(data_dir):
        st.caption("当前目录没有 Gmail 授权。本机授权在项目 data/。练习目录不会拉真邮件，请用 `nexus dashboard` 打开 data/ 再 Sync。")
    else:
        st.caption("Gmail / Calendar 未授权。在终端运行 `python -m nexus.google_auth` 后再 Sync。")

    feeds = load_feeds(data_dir / "feeds.json")
    if feeds:
        st.markdown("### 已保存的课程日历")
        for feed in feeds:
            course = f" · {feed.course}" if feed.course else ""
            st.caption(f"{feed.name} · {feed.kind}{course}")
    session = data_dir / "brightspace_session.json"
    if session.is_file():
        st.caption("本机还有 Brightspace 登录会话。完整课件抓取用：`nexus sync --include-archive`。")

    st.markdown("### 课程日历")
    st.caption("从 Brightspace 复制日历订阅链接。保存后立刻更新今晚的判断。")
    with st.form("brightspace_feed_setup", clear_on_submit=True):
        kind_label = st.radio(
            "Feed type",
            options=["Calendar", "Announcements"],
            index=0,
            horizontal=True,
        )
        name_col, course_col = st.columns(2)
        name = name_col.text_input("Name", placeholder="Operating Systems")
        course = course_col.text_input("Course", placeholder="CS202_OS")
        url = st.text_input(
            "Feed URL",
            type="password",
            placeholder="https://...",
        )
        submitted = st.form_submit_button("Save source", type="primary")

    if submitted:
        feed = FeedSource(
            kind="brightspace_rss" if kind_label == "Announcements" else "brightspace_ical",
            name=name,
            url=url,
            course=course or None,
        )
        try:
            result = connect_calendar_feed(data_dir, feed, sync=run_sync)
        except ValueError as exc:
            st.error(str(exc))
        else:
            receipt = result.get("sync") if isinstance(result.get("sync"), dict) else {}
            action = result["feed"]["action"]
            if receipt.get("status") == "succeeded":
                count = receipt.get("summary", {}).get("task_count", 0)
                st.session_state["setup_notice"] = {
                    "kind": "success",
                    "text": f"Source {action}. Synced {count} tasks. Open Today for the next action.",
                }
            elif receipt.get("status") == "busy":
                st.session_state["setup_notice"] = {
                    "kind": "warning",
                    "text": f"Source {action}. A sync is already running.",
                }
            else:
                st.session_state["setup_notice"] = {
                    "kind": "error",
                    "text": f"Source {action}, but sync did not finish. Check Status.",
                }
            st.rerun()


if __name__ == "__main__":
    main()
