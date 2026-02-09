from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from ..config import default_config
from ..models import Task
from .base import Aggregator

# Permissions we request from the user: Read-only access to Gmail and Calendar.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _ensure_google_deps() -> None:
    """Checks if Google API libraries are installed. Raises error if missing."""
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        import google_auth_httplib2  # noqa: F401
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError(
            "Google API dependencies missing in the current Python environment. "
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
            token_path.write_text(creds.to_json(), encoding="utf-8")
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
        credentials_path: str | None = None,
        include_gmail: bool = True,
        include_calendar: bool = True,
    ) -> None:
        config = default_config()
        self.credentials_path = (
            Path(credentials_path) if credentials_path else config.google_credentials_path
        )
        self.token_path = config.google_token_path
        self.course_aliases = self._load_course_aliases(config.data_dir)
        self.include_gmail = include_gmail
        self.include_calendar = include_calendar

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
        """Gets the last 10 unread emails."""
        response = (
            gmail.users()
            .messages()
            .list(userId="me", labelIds=["UNREAD"], maxResults=10)
            .execute()
        )
        messages = response.get("messages", [])
        for msg in messages:
            # Fetch email details (Subject, Sender, Date) without body content for speed.
            msg_detail = (
                gmail.users()
                .messages()
                .get(userId="me", id=msg["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg_detail.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(no subject)")
            from_line = headers.get("From", "")
            title = f"{subject} — {from_line}".strip()
            snippet = msg_detail.get("snippet")
            
            # Try to guess which course this email belongs to.
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
            
            # Add tags for filtering in the UI.
            tags = ["email", "unread"]
            if is_course_notice:
                tags.append("course_notification")
            else:
                tags.append("general")
            if is_announcement:
                tags.append("announcement")
            yield Task(
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
