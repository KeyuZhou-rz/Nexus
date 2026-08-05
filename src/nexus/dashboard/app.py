from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from ..config import default_config
from ..context_api import build_context
from ..storage import load_tasks, tasks_last_updated
from .styles import DASHBOARD_CSS
from .view_model import (
    build_dashboard_state,
    load_briefing_payload,
    load_pipeline_report,
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
    learning_context = context.get("learning", {})
    state = build_dashboard_state(
        tasks,
        briefing_payload=briefing_payload,
        pipeline_report=pipeline_report,
        sync_sources=sync_sources,
        learning_context=learning_context,
        generated_at=now_local,
        last_updated=tasks_last_updated(),
    )

    _render_sidebar(data_dir=data_dir, briefing_path=briefing_path, report_path=report_path, state=state)
    _render_header(state)

    tab_home, tab_tasks, tab_status = st.tabs(["Home", "Tasks", "Status"])

    with tab_home:
        _render_home(state)

    with tab_tasks:
        _render_tasks(state)

    with tab_status:
        _render_status(state, briefing_path=briefing_path, report_path=report_path)


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

        st.divider()
        st.markdown("### Data Sources")
        st.caption(f"`{data_dir}`")
        st.write(f"Tasks: `{data_dir / 'tasks.json'}`")
        st.write(f"Briefing: `{briefing_path}`")
        st.write(f"Report: `{report_path}`")

        st.divider()
        st.markdown("### What This App Does")
        st.caption("Read-only dashboard for tasks, briefing, and pipeline status.")
        st.caption("Memory, QA, ingest, and archive tooling stay outside this surface.")


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
        _render_learning_summary(state)

        st.markdown('<div class="section-title">Updates</div>', unsafe_allow_html=True)
        if not state.update_items:
            st.markdown(
                '<div class="panel"><div class="mini-note">No new course announcements or grade updates.</div></div>',
                unsafe_allow_html=True,
            )
        else:
            for item in state.update_items:
                st.markdown(render_task_card(item), unsafe_allow_html=True)


def _render_tasks(state) -> None:
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
        _render_learning_summary(state)

    with file_col:
        st.markdown('<div class="section-title">Files</div>', unsafe_allow_html=True)
        st.write(f"Briefing path: `{briefing_path}`")
        st.write(f"Report path: `{report_path}`")
        st.write(f"Snapshot time: `{state.generated_at.isoformat()}`")


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


def _render_learning_summary(state) -> None:
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


if __name__ == "__main__":
    main()
