"""LLM extraction of exams, deadlines and course hints from Gmail bodies.

Pass ``enrich_email_task`` as ``GoogleAggregator(email_enricher=...)``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..core.models import Task

logger = logging.getLogger(__name__)

EXTRACT_STRUCTURED_PROMPT = (
    "Extract structured academic information from this email.\n"
    "Return JSON only with these optional fields (omit if not found):\n"
    "{{\n"
    '  "exam_date": "ISO date or null",\n'
    '  "deadline": "ISO date or null",\n'
    '  "action_items": ["action 1", "action 2"],\n'
    '  "course_hint": "course name or null",\n'
    '  "priority": "high|medium|low",\n'
    '  "summary_zh": "one-line Chinese summary"\n'
    "}}\n\n"
    "Subject: {subject}\n\n"
    "Body:\n{body_text}"
)


def extract_structured_fields(subject: str, body_text: str) -> dict:
    """
    E2: Use LLM to extract exam dates, deadlines, action items from email body.
    Falls back to empty dict if LLM is unavailable or fails.
    """
    if not body_text or len(body_text) < 20:
        return {}

    try:
        from ..knowledge.llm_gateway import gateway
    except Exception:
        return {}

    if not gateway.is_configured:
        return {}

    prompt = EXTRACT_STRUCTURED_PROMPT.format(
        subject=subject,
        body_text=body_text[:3000],  # cap to prevent token overflow
    )
    try:
        response = gateway.chat(
            messages=[
                {"role": "system", "content": "You are an academic email parser. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=400,
            call_site="gmail_extraction",
        )
        data = json.loads(response.content)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if v is not None and v != []}
    except Exception as exc:
        logger.debug(f"LLM structured extraction skipped: {exc}")
    return {}


def enrich_email_task(task: Task, subject: str, body_text: str) -> Task:
    """
    E2: Enrich a Task with LLM-extracted structured fields.
    Modifies task in-place and returns it.
    """
    extracted = extract_structured_fields(subject, body_text)
    if not extracted:
        return task

    # Apply course hint if we don't already have one
    course_hint = extracted.get("course_hint")
    if course_hint and not task.course:
        task.course = str(course_hint)[:50]

    # Apply priority
    priority_map = {"high": 3, "medium": 1, "low": 0}
    if "priority" in extracted:
        task.priority = priority_map.get(str(extracted["priority"]).lower(), 0)

    # Parse exam_date or deadline into due_at
    date_str = extracted.get("exam_date") or extracted.get("deadline")
    if date_str and not task.due_at:
        try:
            task.due_at = datetime.fromisoformat(str(date_str))
        except (ValueError, TypeError):
            pass

    # Prepend Chinese summary to snippet if available
    summary_zh = extracted.get("summary_zh")
    if summary_zh and task.snippet:
        task.snippet = f"[{summary_zh}] {task.snippet}"

    # Add action items as tags
    action_items = extracted.get("action_items", [])
    for item in action_items[:3]:
        if "deadline" in item.lower() or "due" in item.lower():
            if "deadline" not in task.tags:
                task.tags.append("deadline")
        if "exam" in item.lower():
            if "exam" not in task.tags:
                task.tags.append("exam")

    return task
