from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

from ..config import AppConfig, default_config
from ..models import Task
from .base import Aggregator

logger = logging.getLogger(__name__)

# Permissions we request from the user: Read-only access to Gmail and Calendar.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _atomic_write_secret(path: Path, content: str) -> None:
    """Write a credential file atomically with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)


def _ensure_google_deps() -> None:
    """Checks if Google API libraries are installed. Raises error if missing."""
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        import google_auth_httplib2  # noqa: F401
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError(
            "Google API dependencies missing in the current Python environment."
            "Install with: python -m pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib"
        ) from exc



def _load_credentials(token_path: Path, client_secret_path: Path):
    """Loads user login token. Refreshes it automatically if expired."""
    _ensure_google_deps()
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        # If token expired but we have a refresh token, get a new access token.
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _atomic_write_secret(token_path, creds.to_json())
            return creds
        raise RuntimeError(
            "Google token missing or invalid. Run `python -m nexus.google_auth` to authorize."
        )

    return creds


def _build_service(api_name: str, api_version: str, creds):
    """Creates a Google API client for a specific service (e.g., 'gmail', 'calendar')."""
    _ensure_google_deps()
    from googleapiclient.discovery import build

    return build(api_name, api_version, credentials=creds, cache_discovery=False)


class GoogleAggregator(Aggregator):
    """Fetches emails and calendar events from Google."""
    name = "google"

    def __init__(
        self,
        credentials_path: str | Path | None = None,
        include_gmail: bool = True,
        include_calendar: bool = True,
        gmail_max_results: int = 20,
        gmail_window_days: int = 30,
        *,
        config: AppConfig | None = None,
    ) -> None:
        config = config or default_config()
        self.credentials_path = (
            Path(credentials_path) if credentials_path else config.google_credentials_path
        )
        self.token_path = config.google_token_path
        self.course_aliases = self._load_course_aliases(config.data_dir)
        self.include_gmail = include_gmail
        self.include_calendar = include_calendar
        self.gmail_max_results = gmail_max_results
        self.gmail_window_days = gmail_window_days

        if not self.credentials_path or not self.token_path:
            raise RuntimeError("Google credentials or token path not configured.")

    def fetch_tasks(self) -> list[Task]:
        """Main entry point: Connects to Google and gets all data."""
        creds = _load_credentials(self.token_path, self.credentials_path)
        gmail = _build_service("gmail", "v1", creds)
        calendar = _build_service("calendar", "v3", creds)

        tasks: list[Task] = []
        if self.include_gmail:
            tasks.extend(self._fetch_gmail(gmail))
        if self.include_calendar:
            tasks.extend(self._fetch_calendar(calendar))
        return tasks

    def _fetch_gmail(self, gmail) -> Iterable[Task]:
        """E1+E3: Fetch emails with full body (format=full), pagination, and time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.gmail_window_days)
        cutoff_epoch_ms = int(cutoff.timestamp() * 1000)

        fetched = 0
        page_token = None
        while fetched < self.gmail_max_results:
            list_params: dict = {
                "userId": "me",
                "labelIds": ["UNREAD"],
                "maxResults": min(50, self.gmail_max_results - fetched),
                "q": f"after:{cutoff_epoch_ms // 1000}",
            }
            if page_token:
                list_params["pageToken"] = page_token

            response = gmail.users().messages().list(**list_params).execute()
            messages = response.get("messages", [])

            for msg in messages:
                if fetched >= self.gmail_max_results:
                    break
                fetched += 1

                # E1: format="full" to get complete message body
                msg_detail = (
                    gmail.users()
                    .messages()
                    .get(userId="me", id=msg["id"], format="full")
                    .execute()
                )

                headers = {h["name"]: h["value"] for h in msg_detail.get("payload", {}).get("headers", [])}
                subject = headers.get("Subject", "(no subject)")
                from_line = headers.get("From", "")
                title = f"{subject} — {from_line}".strip()

                # E1: Decode full email body
                payload = msg_detail.get("payload", {})
                body_text = self._decode_email_body(payload)

                # Use decoded body as primary text; fall back to snippet
                snippet = body_text if body_text else msg_detail.get("snippet", "")

                course_from_subject = self._extract_course_from_subject(subject)
                course = course_from_subject or self._infer_course(subject, from_line)
                is_course_notice = self._is_course_notification(subject, from_line, course)
                is_announcement = self._is_brightspace_announcement(subject, from_line)
                received_at = None
                internal_date = msg_detail.get("internalDate")
                if internal_date:
                    try:
                        received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
                    except Exception:
                        received_at = None

                tags = ["email", "unread"]
                if is_course_notice:
                    tags.append("course_notification")
                else:
                    tags.append("general")
                if is_announcement:
                    tags.append("announcement")

                task = Task(
                    id=f"gmail:{msg['id']}",
                    title=title,
                    due_at=None,
                    source="gmail",
                    url=f"https://mail.google.com/mail/u/0/#inbox/{msg['id']}",
                    course=course,
                    status="open",
                    priority=0,
                    tags=tags,
                    snippet=snippet,
                    received_at=received_at,
                )

                # E2: LLM structured enrichment (graceful fallback if LLM unavailable)
                if body_text:
                    task = self._enrich_task_from_llm(task, subject, body_text)

                yield task

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    @staticmethod
    def _decode_email_body(payload: dict) -> str:
        """
        E1: Recursively decode Gmail message payload into plain text.

        Handles:
        - text/plain → decode base64url directly
        - text/html → strip HTML tags after decoding
        - multipart/alternative → prefer text/plain, fall back to text/html
        - multipart/mixed → collect all text parts
        """
        mime_type = payload.get("mimeType", "")

        # Recurse into multipart messages
        if mime_type.startswith("multipart/"):
            parts = payload.get("parts", [])
            text_parts: list[str] = []
            html_parts: list[str] = []
            for part in parts:
                decoded = GoogleAggregator._decode_email_body(part)
                if not decoded:
                    continue
                if part.get("mimeType") == "text/html":
                    html_parts.append(decoded)
                else:
                    text_parts.append(decoded)
            # Prefer text/plain; fall back to text/html
            if text_parts:
                return "\n\n".join(text_parts)
            if html_parts:
                return "\n\n".join(html_parts)
            return ""

        # Single-part: decode body data
        body = payload.get("body", {})
        data = body.get("data", "")
        if not data:
            return ""

        try:
            decoded_bytes = base64.urlsafe_b64decode(data)
            text = decoded_bytes.decode("utf-8", errors="replace")
        except Exception:
            return ""

        # Strip HTML if needed
        if mime_type == "text/html":
            text = GoogleAggregator._strip_html(text)

        return text.strip()

    @staticmethod
    def _strip_html(html_text: str) -> str:
        """E1: Minimal HTML tag stripper — removes tags, decodes entities, collapses whitespace."""
        # Remove style/script blocks entirely
        text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
        # Replace <br>, <p>, <div>, <li>, <tr> with newlines
        text = re.sub(r"<(br|p|div|li|tr)[^>]*/?>", "\n", text, flags=re.IGNORECASE)
        # Remove all other tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Decode common HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        # Collapse whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _fetch_calendar(self, calendar) -> Iterable[Task]:
        """Gets calendar events for the next 7 days."""
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=7)
        calendars = self._list_calendars(calendar)
        for cal_id, cal_name in calendars:
            # 'singleEvents=True' expands recurring events (like weekly classes) into individual items.
            events_result = (
                calendar.events()
                .list(
                    calendarId=cal_id,
                    timeMin=now.isoformat(),
                    timeMax=horizon.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=50,
                )
                .execute()
            )
            events = events_result.get("items", [])
            for event in events:
                start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
                # Parse the start time.
                due_at = None
                if start:
                    try:
                        due_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    except ValueError:
                        due_at = None
                seed = f"{cal_id}:{event.get('id', '')}:{start}"
                event_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
                yield Task(
                    id=f"gcal:{event_id}",
                    title=event.get("summary", "(no title)"),
                    due_at=due_at,
                    source="gcal",
                    url=event.get("htmlLink"),
                    course=cal_name,
                    status="open",
                    priority=0,
                    tags=["calendar"],
                )

    def _list_calendars(self, calendar) -> list[tuple[str, str]]:
        """Finds all calendars the user has selected/checked in Google Calendar UI."""
        calendars: list[tuple[str, str]] = []
        page_token = None
        while True:
            response = calendar.calendarList().list(pageToken=page_token).execute()
            for item in response.get("items", []):
                if not item.get("selected", False) and not item.get("primary", False):
                    continue
                cal_id = item.get("id")
                if not cal_id:
                    continue
                name = item.get("summary") or "Calendar"
                calendars.append((cal_id, name))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        if not calendars:
            calendars.append(("primary", "Primary"))
        return calendars

    @staticmethod
    def _load_course_aliases(data_dir: Path) -> list[dict]:
        """Loads the alias mapping file to help identify courses from email subjects."""
        aliases_path = data_dir / "course_aliases.json"
        if not aliases_path.exists():
            return []
        try:
            payload = json.loads(aliases_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return payload

    def _infer_course(self, subject: str, from_line: str) -> str | None:
        """Matches email subject/sender against known course aliases."""
        haystack = f"{subject} {from_line}".lower()
        for entry in self.course_aliases:
            course = str(entry.get("course", "")).strip()
            aliases = entry.get("aliases", [])
            if not course:
                continue
            for alias in aliases:
                if str(alias).lower() in haystack:
                    return course
        return None

    @staticmethod
    def _is_course_notification(subject: str, from_line: str, course: str | None) -> bool:
        """Heuristic: Is this email likely related to school work?"""
        if course:
            return True
        haystack = f"{subject} {from_line}".lower()
        hints = ("brightspace", "d2l", "learning management", "announcement")
        if any(hint in haystack for hint in hints):
            return True
        if "noreply" in from_line.lower() and "nyu" in from_line.lower():
            return True
        return False

    @staticmethod
    def _is_brightspace_announcement(subject: str, from_line: str) -> bool:
        """Heuristic: Is this specifically a Brightspace announcement?"""
        subj = subject.lower()
        sender = from_line.lower()
        if "brightspace" in sender or "mail.brightspace.nyu.edu" in sender:
            return "announcement" in subj
        return False

    @staticmethod
    def _extract_course_from_subject(subject: str) -> str | None:
        """Extracts course name from standard announcement patterns."""
        # Example: "Operating Systems - Spring 2026 - Announcements: ..."
        # Example: "Multivariable Calculus - Announcements: assignment"
        pattern = r"^(?P<course>.+?)\s+[-–]\s+Announcements?:"
        match = re.match(pattern, subject, flags=re.IGNORECASE)
        if match:
            course = match.group("course").strip()
            return course or None
        return None

    # ── E2: LLM Structured Extraction ──────────────────────────────────

    _EXTRACT_STRUCTURED_PROMPT = (
        "Extract structured academic information from this email.\n"
        "Return JSON only with these optional fields (omit if not found):\n"
        "{\n"
        '  "exam_date": "ISO date or null",\n'
        '  "deadline": "ISO date or null",\n'
        '  "action_items": ["action 1", "action 2"],\n'
        '  "course_hint": "course name or null",\n'
        '  "priority": "high|medium|low",\n'
        '  "summary_zh": "one-line Chinese summary"\n'
        "}\n\n"
        "Subject: {subject}\n\n"
        "Body:\n{body_text}"
    )

    def _extract_structured_fields(
        self, subject: str, body_text: str
    ) -> dict:
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

        prompt = self._EXTRACT_STRUCTURED_PROMPT.format(
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

    def _enrich_task_from_llm(self, task: Task, subject: str, body_text: str) -> Task:
        """
        E2: Enrich a Task with LLM-extracted structured fields.
        Modifies task in-place and returns it.
        """
        extracted = self._extract_structured_fields(subject, body_text)
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
