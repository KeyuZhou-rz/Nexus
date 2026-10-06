"""
Brightspace LMS Connector — Nexus Phase X2

Implements LMSConnector using Brightspace iCal feeds (assignments/deadlines),
RSS feeds (announcements), and archive_sync scraped data (files/resources).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from ..utils import stable_id
from .brightspace import (
    FeedConfig,
    _ensure_deps,
    _normalize_dt,
)
from .lms_base import (
    Announcement,
    Assignment,
    Course,
    CourseFile,
    LMSConnector,
)

logger = logging.getLogger(__name__)


class BrightspaceLMSConnector(LMSConnector):
    """
    LMSConnector backed by Brightspace iCal + RSS feed URLs.

    Usage:
        ical = [FeedConfig(name="OS", url=".../basic.ics", course="CS202")]
        rss  = [FeedConfig(name="OS", url=".../rss.xml", course="CS202")]
        connector = BrightspaceLMSConnector(ical_feeds=ical, rss_feeds=rss)
        courses = connector.list_courses()
        assignments = connector.list_assignments("CS202")
    """

    name = "brightspace"

    def __init__(
        self,
        ical_feeds: Iterable[FeedConfig] | None = None,
        rss_feeds: Iterable[FeedConfig] | None = None,
        archive_dir: Path | None = None,
    ) -> None:
        self.ical_feeds: list[FeedConfig] = list(ical_feeds or [])
        self.rss_feeds: list[FeedConfig] = list(rss_feeds or [])
        self.archive_dir = archive_dir  # for list_files

    # ── LMSConnector interface ──────────────────────────────────────

    def list_courses(self) -> list[Course]:
        """Extract unique courses from configured feeds."""
        seen: set[str] = set()
        courses: list[Course] = []
        for feed in self.ical_feeds + self.rss_feeds:
            cid = feed.course or feed.name
            if not cid or cid in seen:
                continue
            seen.add(cid)
            courses.append(Course(
                course_id=cid,
                name=feed.name or cid,
                url=feed.url if feed.url else None,
            ))
        return courses

    def list_assignments(self, course_id: str) -> list[Assignment]:
        """Parse iCal feeds for assignments/deadlines in a course."""
        if not self.ical_feeds:
            return []
        _ensure_deps()
        from icalendar import Calendar

        assignments: list[Assignment] = []
        for feed in self.ical_feeds:
            feed_course = feed.course or feed.name
            if feed_course != course_id:
                continue
            cal = self._load_ical(feed)
            for component in cal.walk():
                if component.name not in {"VEVENT", "VTODO"}:
                    continue
                summary = str(component.get("SUMMARY", "")).strip()
                uid = str(component.get("UID", "")).strip()
                if not summary:
                    continue
                due_raw = component.get("DUE") or component.get("DTSTART") or component.get("DTEND")
                due_at = _normalize_dt(due_raw)
                url = str(component.get("URL", "")) or None
                description = str(component.get("DESCRIPTION", ""))[:500] or None

                aid = uid or stable_id(f"{summary}|{due_at}|{feed.url}")
                assignments.append(Assignment(
                    assignment_id=aid,
                    title=summary,
                    course_id=course_id,
                    due_at=due_at,
                    url=url,
                    description=description,
                ))
        return assignments

    def list_announcements(self, course_id: str) -> list[Announcement]:
        """Parse RSS feeds for announcements in a course."""
        if not self.rss_feeds:
            return []
        _ensure_deps()
        import feedparser
        import requests

        announcements: list[Announcement] = []
        for feed in self.rss_feeds:
            feed_course = feed.course or feed.name
            if feed_course != course_id:
                continue
            try:
                resp = requests.get(feed.url, timeout=20)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
            except Exception as exc:
                logger.warning(f"RSS fetch failed for {feed.url}: {exc}")
                continue

            for entry in parsed.entries:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                eid = entry.get("id") or stable_id(f"{title}|{feed.url}")
                posted_at = None
                published = entry.get("published") or entry.get("updated")
                if published and hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        posted_at = datetime(*entry.published_parsed[:6])
                    except Exception:
                        posted_at = None
                body = entry.get("summary") or entry.get("description") or ""
                link = entry.get("link")

                announcements.append(Announcement(
                    announcement_id=str(eid),
                    title=title,
                    course_id=course_id,
                    body=body[:500] if body else None,
                    posted_at=posted_at,
                    url=link,
                ))
        return announcements

    def list_files(self, course_id: str) -> list[CourseFile]:
        """List archived files for a course (from archive_sync scrapes)."""
        if not self.archive_dir:
            return []
        course_dir = self.archive_dir / course_id
        if not course_dir.exists() or not course_dir.is_dir():
            return []
        files: list[CourseFile] = []
        for fpath in sorted(course_dir.iterdir()):
            if fpath.is_file():
                stat = fpath.stat()
                files.append(CourseFile(
                    file_id=stable_id(str(fpath)),
                    name=fpath.name,
                    course_id=course_id,
                    url=fpath.as_uri(),
                    content_type=_guess_mime(fpath.suffix),
                    size_bytes=int(stat.st_size),
                ))
        return files

    # ── internal ────────────────────────────────────────────────────

    def _load_ical(self, feed: FeedConfig):
        """Load iCal from URL or file path."""
        _ensure_deps()
        import requests
        from icalendar import Calendar

        url = feed.url
        parsed = urlparse(url)
        if parsed.scheme == "file":
            path = Path(parsed.path)
            if not path.exists():
                raise FileNotFoundError(f"iCal file not found: {path}")
            return Calendar.from_ical(path.read_bytes())
        if parsed.scheme in {"http", "https"}:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            return Calendar.from_ical(resp.content)
        path = Path(url)
        if not path.exists():
            raise FileNotFoundError(f"iCal file not found: {path}")
        return Calendar.from_ical(path.read_bytes())


def _guess_mime(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(suffix.lower(), "application/octet-stream")
