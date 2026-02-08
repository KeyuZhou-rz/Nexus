from __future__ import annotations

from pathlib import Path
import importlib
import sys

import streamlit as st

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from nexus.aggregation import check_brightspace_feeds, run_aggregation  # noqa: E402
from nexus.config import default_config  # noqa: E402
from nexus.storage import load_projects, load_tasks  # noqa: E402


def _load_briefing():
    try:
        module = importlib.import_module("nexus.intelligence.briefing")
        module = importlib.reload(module)
        build = getattr(module, "build_briefing", None)
        if build is None:
            raise ImportError("build_briefing not found in nexus.intelligence.briefing")
        return build, None
    except Exception as exc:  # pragma: no cover - UI safety net
        return None, str(exc)


def _get_query_params() -> dict[str, list[str]]:
    if hasattr(st, "query_params"):
        params = st.query_params
        try:
            return {k: list(v) if isinstance(v, (list, tuple)) else [str(v)] for k, v in params.items()}
        except Exception:
            try:
                raw = params.to_dict()
                return {
                    k: list(v) if isinstance(v, (list, tuple)) else [str(v)]
                    for k, v in raw.items()
                }
            except Exception:
                return {}
    if hasattr(st, "experimental_get_query_params"):
        return st.experimental_get_query_params()
    return {}


def _set_query_params(**kwargs: str) -> None:
    if hasattr(st, "query_params"):
        params = st.query_params
        try:
            params.clear()
            for key, value in kwargs.items():
                params[key] = value
        except Exception:
            pass
        return
    if hasattr(st, "experimental_set_query_params"):
        st.experimental_set_query_params(**kwargs)


def _rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
        return
    if hasattr(st, "experimental_rerun"):
        st.experimental_rerun()

st.set_page_config(page_title="Nexus", page_icon="🧭", layout="wide")

st.title("Nexus")
st.caption("Personal command center (Phase 1 skeleton)")

col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("Workspace Orchestrator")
    projects = load_projects()
    if not projects:
        st.info("No projects found. Add entries to data/projects.json.")
    else:
        for project in projects:
            st.write(f"{project.name}")
            st.caption(f"{project.path} | {project.ide}")
            st.divider()

with col_right:
    st.subheader("Daily Briefing")
    config = default_config()
    tasks = load_tasks()
    build_briefing, briefing_error = _load_briefing()
    if build_briefing is None:
        st.warning(f"Briefing module not available: {briefing_error}")
        briefing = None
    else:
        briefing = build_briefing(tasks, window_days=7, config=config)

    if briefing is None:
        st.caption("Briefing unavailable. Fix the import error above to enable summaries.")
    else:
        params = _get_query_params()
        detail_id = params.get("detail", [None])[0]

        if detail_id and detail_id in briefing.task_index:
            task = briefing.task_index[detail_id]
            st.markdown("**Detail**")
            st.write(task.title)
            if task.course:
                st.caption(task.course)
            if task.due_at:
                st.write(f"Time: {task.due_at.isoformat()}")
            if task.received_at:
                st.write(f"Received: {task.received_at.isoformat()}")
            if task.snippet:
                st.write(task.snippet)
            if task.url:
                st.markdown(f"[Open original]({task.url})")
            if st.button("Back to briefing"):
                _set_query_params()
                _rerun()
        else:
            st.write("Red Zone")

            if briefing.warnings:
                for warning in briefing.warnings:
                    st.warning(warning)

            st.markdown("**待办 / To-do**")
            if not briefing.todo:
                st.caption("No to-dos in the next 7 days.")
            else:
                for idx, item in enumerate(briefing.todo):
                    st.write(item.text_en)
                    st.caption(item.text_zh)
                    if item.source_ids:
                        if st.button("Open details", key=f"todo-detail-{idx}"):
                            _set_query_params(detail=item.source_ids[0])
                            _rerun()

            st.markdown("**日程 / Schedule**")
            if not briefing.schedule:
                st.caption("No schedule items in the next 7 days.")
            else:
                for idx, item in enumerate(briefing.schedule):
                    if item.due_at:
                        st.write(f"{item.due_at.strftime('%Y-%m-%d %H:%M')} — {item.text_en}")
                    else:
                        st.write(item.text_en)
                    st.caption(item.text_zh)
                    if item.source_ids:
                        if st.button("Open details", key=f"schedule-detail-{idx}"):
                            _set_query_params(detail=item.source_ids[0])
                            _rerun()

    st.divider()
    st.subheader("Google Status")
    cred_path = config.google_credentials_path or Path("")
    token_path = config.google_token_path or Path("")
    st.write(f"Client secret: {'OK' if cred_path.exists() else 'Missing'}")
    st.write(f"Token: {'OK' if token_path.exists() else 'Missing'}")
    st.code("python -m nexus.google_auth", language="bash")

    st.divider()
    st.subheader("Aggregation")
    if st.button("Run Aggregation (Google + Brightspace)"):
        with st.spinner("Aggregating..."):
            result = run_aggregation(include_google=True)
        if result.errors:
            for err in result.errors:
                st.error(err)
        st.success(f"Aggregated {len(result.tasks)} items.")

        gmail_tasks = [t for t in result.tasks if t.source == "gmail"]
        cal_tasks = [t for t in result.tasks if t.source == "gcal"]
        bspace_tasks = [
            t
            for t in result.tasks
            if t.source == "brightspace" and "llm_only" not in t.tags
        ]

        if gmail_tasks:
            course_mail = [t for t in gmail_tasks if "course_notification" in t.tags]
            other_mail = [t for t in gmail_tasks if "course_notification" not in t.tags]
            if course_mail:
                st.markdown("**Gmail (Course Notifications)**")
                for t in course_mail:
                    st.write(f"- {t.title}")
            if other_mail:
                st.markdown("**Gmail (Other)**")
                for t in other_mail:
                    st.write(f"- {t.title}")

        if cal_tasks:
            st.markdown("**Calendar (Next 7 Days)**")
            for t in cal_tasks:
                when = t.due_at.isoformat() if t.due_at else "TBD"
                st.write(f"- {when} — {t.title}")

        if bspace_tasks:
            st.markdown("**Brightspace**")
            grouped: dict[str, list] = {}
            for t in bspace_tasks:
                course = t.course or "Uncategorized"
                grouped.setdefault(course, []).append(t)
            for course, items in sorted(grouped.items()):
                st.markdown(f"**{course}**")
                for t in items:
                    when = t.due_at.isoformat() if t.due_at else "TBD"
                    st.write(f"- {when} — {t.title}")

    st.divider()
    st.subheader("Feed Diagnostics")
    if st.button("Test Brightspace Feeds"):
        statuses = check_brightspace_feeds()
        if not statuses:
            st.info("No feeds configured. Update data/feeds.json.")
        for status in statuses:
            label = f"{status.name} ({status.kind})"
            if not status.enabled:
                st.write(f"⏸️ {label} — disabled")
            elif status.ok:
                st.write(f"✅ {label} — items: {status.item_count}")
            else:
                st.write(f"❌ {label} — error: {status.error}")
