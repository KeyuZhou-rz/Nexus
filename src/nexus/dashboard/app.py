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

from ..academic_state import display_course, refresh_from_snapshot, tasks_from_obligations
from ..changes import acknowledge_change, change_key, list_inbox_changes
from ..course_state import build_course_states
from ..intelligence.workload import build_judgment, resolve_effort
from ..tonight_plan import build_tonight_plan
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
from .styles import DASHBOARD_CSS
from .view_model import (
    build_dashboard_state,
    load_briefing_payload,
    load_pipeline_report,
    render_task_card,
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
    judgment = build_judgment(
        tasks,
        done_ids=completed_task_ids(completions),
        now=now_local,
        learning_focus=learning_context.get("focus") if isinstance(learning_context.get("focus"), list) else None,
        completions=completions,
    )
    changes = list_inbox_changes(data_dir, limit=5, tasks=snapshot)
    plan = build_tonight_plan(
        tasks,
        now=now_local,
        completions=completions,
        focus_id=judgment.anchor_task_id,
    )
    courses = build_course_states(tasks, completions=completions, now=now_local)
    state = build_dashboard_state(
        tasks,
        briefing_payload=briefing_payload,
        pipeline_report=pipeline_report,
        sync_sources=sync_sources,
        sync_receipt=sync_receipt,
        learning_context=learning_context,
        generated_at=now_local,
        last_updated=tasks_last_updated(),
        anchor_task_id=judgment.anchor_task_id,
    )

    _render_sidebar(data_dir=data_dir, state=state, include_google=_google_authorized(data_dir))

    tab_home, tab_tasks, tab_setup = st.tabs(["Tonight", "Tasks", "Setup"])

    with tab_home:
        _render_briefing(judgment, tasks, data_dir, state, changes, plan, courses)

    with tab_tasks:
        _render_tasks(state, data_dir=data_dir, tasks=tasks)

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


def _render_briefing(judgment, tasks, data_dir, state, changes, plan, courses) -> None:
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        if judgment is None or not judgment.available:
            st.markdown(
                '<div class="briefing"><p class="briefing-kicker">Nexus</p>'
                "<p>还没有作业或考试截止。</p>"
                "<p class=\"briefing-muted\">到 Setup 贴上课程日历，或用下面的 Add task 加上一条。</p></div>",
                unsafe_allow_html=True,
            )
        else:
            blocks = ['<p class="briefing-kicker">Nexus</p>', f'<p class="headline">{_html(judgment.deadline)}</p>']
            if plan is not None and plan.available and plan.text:
                blocks.append(f"<p class=\"suggestion\">{_html(plan.text)}</p>")
            elif judgment.suggestion:
                blocks.append(f"<p class=\"suggestion\">建议：{_html(judgment.suggestion)}</p>")
            if judgment.pace:
                blocks.append(f"<p>{_html(judgment.pace)}</p>")
            if judgment.forecast:
                blocks.append(f"<p>{_html(judgment.forecast)}</p>")
            info = [item for item in changes if not item.needs_decision]
            if info:
                items = "".join(f"<li>{_html(item.text)}</li>" for item in info)
                blocks.append(f"<p>世界变化</p><ul>{items}</ul>")
            st.markdown(f'<div class="briefing">{"".join(blocks)}</div>', unsafe_allow_html=True)
        _render_change_decisions(changes, data_dir)
        _render_add_task(data_dir, form_key="tonight_add_task")
        _render_obligation_blocks(judgment, tasks, data_dir)
    with right:
        _render_course_states(courses)
        _render_schedule(state)


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


def _render_obligation_blocks(judgment, tasks, data_dir) -> None:
    now_local = datetime.now().astimezone()
    open_tasks = _open_obligations(tasks)
    if not open_tasks:
        st.caption("没有未完成的义务。用上面的 Add task 加上作业或考试。")
        return

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
        recommended = 0 if judgment is not None and task.id == judgment.anchor_task_id else 1
        due = localize_dt(task.due_at, now_local)
        stamp = due.timestamp() if due is not None else float("inf")
        return (recommended, stamp)

    this_week.sort(key=sort_key)
    later.sort(key=sort_key)
    completions = load_completions(data_dir)
    running = any(
        isinstance(completions.get(task.id), dict)
        and completions[task.id].get("started_at")
        and not completions[task.id].get("done_at")
        for task in open_tasks
    )
    if running:
        st.caption("计时绑在你点开始做的那一条上。做完点同一条的完成。")
    else:
        st.caption("和 Tasks 是同一份未完成义务。每条都可以开始做。")

    if this_week:
        st.markdown('<div class="section-title">这周你还有：</div>', unsafe_allow_html=True)
        for task in this_week:
            _render_task_block(task, judgment, completions, data_dir, now_local)
    if later:
        st.markdown('<div class="section-title">更晚：</div>', unsafe_allow_html=True)
        for task in later:
            _render_task_block(task, judgment, completions, data_dir, now_local)
    if undated:
        st.markdown('<div class="section-title">未定期：</div>', unsafe_allow_html=True)
        for task in undated:
            _render_task_block(task, judgment, completions, data_dir, now_local)


def _render_task_block(task, judgment, completions, data_dir, now_local) -> None:
    entry = completions.get(task.id) if isinstance(completions.get(task.id), dict) else {}
    started = bool(entry.get("started_at") and not entry.get("done_at"))
    elapsed = _elapsed_minutes(entry) if started else None
    recommended = judgment is not None and task.id == judgment.anchor_task_id
    hours, _basis = resolve_effort(task, completions)
    remaining = getattr(task, "remaining_hours", None)
    spent = getattr(task, "spent_hours", None)
    estimated = getattr(task, "estimated_hours", None)
    kind = "week-block recommended" if recommended else "week-block"
    badge = "建议今晚" if recommended else ""
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
    action_cols = st.columns(2)
    with action_cols[0]:
        if st.button("开始做", key=f"start_{task.id}", disabled=started):
            start_task(data_dir, task.id)
            persist_next_action(data_dir=data_dir)
            st.rerun()
    with action_cols[1]:
        if st.button(
            "完成",
            key=f"done_{task.id}",
            type="primary" if recommended or started else "secondary",
        ):
            mark_task_done(data_dir, task.id)
            persist_next_action(data_dir=data_dir)
            st.rerun()


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
            course_col, priority_col = st.columns(2)
            course = course_col.text_input("Course", placeholder="CS202_OS")
            priority_label = priority_col.selectbox(
                "Priority", ["Normal", "Low", "High", "Urgent"], index=0
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
            submitted = st.form_submit_button("Add task", type="primary")

        if submitted:
            priority = {"Low": 0, "Normal": 1, "High": 2, "Urgent": 3}[priority_label]
            deadline = (
                datetime.combine(due_date, due_time).astimezone()
                if has_deadline and due_date is not None and due_time is not None
                else None
            )
            try:
                add_manual_task(
                    data_dir,
                    title=title,
                    due_at=deadline,
                    course=course,
                    priority=priority,
                    tags=tags.split(","),
                    notes=notes,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                persist_next_action(data_dir=data_dir)
                st.success("已加入义务，Tonight 和 Tasks 会一起更新。")
                st.rerun()


def _render_tasks(state, *, data_dir: Path, tasks) -> None:
    st.caption("和 Tonight 同一份未完成义务。完成请在 Tonight 点。")
    _render_add_task(data_dir, form_key="manual_task")

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
                    "text": f"Source {action}. Synced {count} tasks. Open Home for tonight's next action.",
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
