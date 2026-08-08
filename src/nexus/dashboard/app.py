from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pandas as pd
import streamlit as st

from ..config import default_config
from ..context_api import build_context
from ..models import FeedSource
from ..onboarding import initialize_runtime, upsert_feed
from ..storage import load_tasks, tasks_last_updated
from ..sync_service import load_sync_receipt, run_sync
from ..task_service import add_manual_task
from .styles import DASHBOARD_CSS
from .view_model import (
    build_dashboard_state,
    load_briefing_payload,
    load_pipeline_report,
    render_learning_focus_card,
    render_task_card,
)


def main() -> None:
    st.set_page_config(page_title="Nexus Dashboard", page_icon="🧭", layout="wide")
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    config = default_config()
    data_dir = config.data_dir
    briefing_path = data_dir / "briefing.json"
    report_path = data_dir / "pipeline_report.json"

    tasks = load_tasks()
    briefing_payload = load_briefing_payload(briefing_path)
    pipeline_report = load_pipeline_report(report_path)
    now_local = datetime.now().astimezone()
    context = build_context(data_dir=data_dir, now=now_local)
    sync_sources = context.get("sync", {}).get("sources", {})
    sync_receipt = load_sync_receipt(data_dir)
    learning_context = context.get("learning", {})
    state = build_dashboard_state(
        tasks,
        briefing_payload=briefing_payload,
        pipeline_report=pipeline_report,
        sync_sources=sync_sources,
        sync_receipt=sync_receipt,
        learning_context=learning_context,
        generated_at=now_local,
        last_updated=tasks_last_updated(),
    )

    _render_sidebar(data_dir=data_dir, briefing_path=briefing_path, report_path=report_path, state=state)
    _render_header(state)

    tab_home, tab_tasks, tab_status, tab_setup = st.tabs(["Home", "Tasks", "Status", "Setup"])

    with tab_home:
        _render_home(state)

    with tab_tasks:
        _render_tasks(state, data_dir=data_dir)

    with tab_status:
        _render_status(state, briefing_path=briefing_path, report_path=report_path)

    with tab_setup:
        _render_setup(data_dir, state)


def _render_sidebar(*, data_dir: Path, briefing_path: Path, report_path: Path, state) -> None:
    with st.sidebar:
        st.markdown("### Snapshot")
        st.caption(state.last_updated_text)
        if state.pipeline_ok is True:
            st.markdown('<div class="status-good">Pipeline healthy</div>', unsafe_allow_html=True)
        elif state.pipeline_ok is False:
            st.markdown('<div class="status-bad">Pipeline needs attention</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="status-warn">No pipeline report yet</div>', unsafe_allow_html=True)

        if st.button("Sync now", type="primary", use_container_width=True):
            with st.spinner("Syncing configured sources..."):
                receipt = run_sync(data_dir=data_dir, trigger="dashboard")
            st.session_state["sync_notice"] = {
                "status": receipt.get("status"),
                "task_count": receipt.get("summary", {}).get("task_count", 0),
            }
            st.rerun()

        notice = st.session_state.pop("sync_notice", None)
        if isinstance(notice, dict):
            if notice.get("status") == "succeeded":
                st.success(f"Sync complete. {notice.get('task_count', 0)} tasks in context.")
            elif notice.get("status") == "busy":
                st.warning("A sync is already running.")
            else:
                st.error("Sync finished with errors. Check Status for details.")

        st.divider()
        st.markdown("### Data Sources")
        st.caption(f"`{data_dir}`")
        st.write(f"Tasks: `{data_dir / 'tasks.json'}`")
        st.write(f"Briefing: `{briefing_path}`")
        st.write(f"Report: `{report_path}`")

        st.divider()
        st.markdown("### What This App Does")
        st.caption("Dashboard for tasks, briefing, source health, and learning memory.")
        st.caption("Setup writes source configuration. Tasks can also add user-owned local items.")


def _render_header(state) -> None:
    chip_values = [
        f"{state.snapshot.overdue} overdue",
        f"{state.snapshot.due_today} due today",
        f"{state.snapshot.next_seven_days} in 7 days",
        f"{state.snapshot.recent_updates} recent updates",
    ]
    chips_html = "".join(f'<div class="chip">{value}</div>' for value in chip_values)
    st.markdown(
        (
            '<div class="hero">'
            '<div class="eyebrow">Dashboard V1</div>'
            f'<h1 class="hero-title">{state.greeting}</h1>'
            f'<div class="hero-sub">{state.last_updated_text}. {state.pipeline_summary}</div>'
            f'<div class="chip-row">{chips_html}</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_home(state) -> None:
    metric_cols = st.columns(4)
    metric_cols[0].metric("Total Tasks", state.snapshot.total_tasks)
    metric_cols[1].metric("Overdue", state.snapshot.overdue)
    metric_cols[2].metric("Due Today", state.snapshot.due_today)
    metric_cols[3].metric("Recent Updates", state.snapshot.recent_updates)

    left_col, right_col = st.columns([1.25, 1], gap="large")

    with left_col:
        st.markdown('<div class="section-title">Focus</div>', unsafe_allow_html=True)
        if not state.focus_items:
            st.info("No action items yet. Sync data or wait for the next update cycle.")
        else:
            for item in state.focus_items:
                st.markdown(render_task_card(item), unsafe_allow_html=True)

        st.markdown('<div class="section-title">Upcoming</div>', unsafe_allow_html=True)
        if not state.upcoming_groups:
            st.info("No scheduled items in the current snapshot.")
        else:
            for group in state.upcoming_groups:
                st.markdown(f'<div class="group-label">{group.label}</div>', unsafe_allow_html=True)
                for item in group.items[:6]:
                    st.markdown(render_task_card(item), unsafe_allow_html=True)

    with right_col:
        _render_learning_focus(state)

        st.markdown('<div class="section-title">Briefing</div>', unsafe_allow_html=True)
        if state.briefing_lines:
            st.markdown('<div class="panel"><div class="panel-kicker">Today\'s summary</div>', unsafe_allow_html=True)
            for line in state.briefing_lines:
                st.write(f"- {line}")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="panel"><div class="panel-kicker">Today\'s summary</div>'
                '<div class="mini-note">No briefing snapshot yet.</div></div>',
                unsafe_allow_html=True,
            )

        if state.briefing_warnings:
            for warning in state.briefing_warnings:
                st.warning(warning)

        _render_sync_summary(state)
        st.markdown('<div class="section-title">Updates</div>', unsafe_allow_html=True)
        if not state.update_items:
            st.markdown(
                '<div class="panel"><div class="mini-note">No new course announcements or grade updates.</div></div>',
                unsafe_allow_html=True,
            )
        else:
            for item in state.update_items:
                st.markdown(render_task_card(item), unsafe_allow_html=True)


def _render_tasks(state, *, data_dir: Path) -> None:
    with st.expander("Add task", expanded=False):
        with st.form("manual_task", clear_on_submit=True):
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
                st.success("Task added.")
                st.rerun()

    filter_cols = st.columns(4)
    course = filter_cols[0].selectbox("Course", ["All"] + state.available_courses, index=0)
    source = filter_cols[1].selectbox("Source", ["All"] + state.available_sources, index=0)
    status = filter_cols[2].selectbox("Status", ["All", "open", "in_progress", "done", "blocked"], index=0)
    tag = filter_cols[3].selectbox("Tag", ["All"] + state.available_tags, index=0)

    rows = state.tasks_table
    if course != "All":
        rows = [row for row in rows if row["course"] == course]
    if source != "All":
        rows = [row for row in rows if row["source"] == source]
    if status != "All":
        rows = [row for row in rows if row["status"] == status]
    if tag != "All":
        rows = [row for row in rows if tag in {part.strip() for part in row["tags"].split(",") if part.strip()}]

    st.caption(f"{len(rows)} task(s) in current view")
    if not rows:
        st.info("No tasks match the current filters.")
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
    st.markdown('<div class="section-title">Learning Focus</div>', unsafe_allow_html=True)
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
    st.markdown('<div class="section-title">Local runtime</div>', unsafe_allow_html=True)
    st.caption(str(data_dir))
    if st.button("Initialize local data", type="secondary", use_container_width=False):
        result = initialize_runtime(data_dir)
        if result["ready"]:
            st.success("Local runtime is ready.")
        else:
            for warning in result["warnings"]:
                st.error(str(warning))

    st.markdown('<div class="section-title">Source readiness</div>', unsafe_allow_html=True)
    _render_sync_table(state)

    st.markdown('<div class="section-title">Brightspace feed</div>', unsafe_allow_html=True)
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
            result = upsert_feed(data_dir, feed)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(f"Source {result['action']}.")
            st.rerun()


if __name__ == "__main__":
    main()
