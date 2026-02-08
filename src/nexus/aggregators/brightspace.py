from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Iterable
import hashlib

from ..models import Task
from .base import Aggregator


def _ensure_deps() -> None:
    try:
        import requests  # noqa: F401
        import feedparser  # noqa: F401
        import icalendar  # noqa: F401
    except Exception as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError(
            "Brightspace dependencies missing. Install with: python -m pip install "
            "requests feedparser icalendar"
        ) from exc


def _normalize_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "dt"):
        value = value.dt
    if isinstance(value, datetime):
        return value
    if hasattr(value, "timetuple"):
        try:
            return datetime.combine(value, time(23, 59))
        except Exception:
            return None
    return None


def _stable_id(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _is_exam_title(title: str) -> bool:
    lowered = title.lower()
    keywords = ("exam", "midterm", "final", "quiz", "test")
    return any(word in lowered for word in keywords)


def _within_window(due_at: datetime | None, days_ahead: int = 180) -> bool:
    if due_at is None:
        return False
    now = datetime.now(tz=due_at.tzinfo)
    return now <= due_at <= (now + timedelta(days=days_ahead))


@dataclass
class FeedConfig:
    name: str
    url: str
    course: str | None = None
    audience: str = "ui"
    mode: str = "default"


class BrightspaceAggregator(Aggregator):
    name = "brightspace"

    def __init__(
        self,
        ical_feeds: Iterable[FeedConfig] | None = None,
        rss_feeds: Iterable[FeedConfig] | None = None,
    ) -> None:
        self.ical_feeds = list(ical_feeds or [])
        self.rss_feeds = list(rss_feeds or [])

    def fetch_tasks(self) -> list[Task]:
        tasks: list[Task] = []
        tasks.extend(self._fetch_ical_tasks())
        tasks.extend(self._fetch_rss_tasks())
        return tasks

    def _fetch_ical_tasks(self) -> list[Task]:
        if not self.ical_feeds:
            return []
        _ensure_deps()
        from icalendar import Calendar

        tasks: list[Task] = []
        for feed in self.ical_feeds:
            cal = self._load_ical(feed)
            cal_name = cal.get("X-WR-CALNAME")
            for component in cal.walk():
                if component.name not in {"VEVENT", "VTODO"}:
                    continue
                summary = str(component.get("SUMMARY", "")).strip()
                uid = str(component.get("UID", "")).strip()
                due_raw = component.get("DUE") or component.get("DTSTART") or component.get("DTEND")
                due_at = _normalize_dt(due_raw)
                url = component.get("URL")
                course = feed.course or feed.name or str(cal_name or "").strip() or None
                if not summary:
                    continue
                if feed.mode == "exams_only":
                    if not _is_exam_title(summary):
                        continue
                    if not _within_window(due_at):
                        continue
                seed = uid or f"{summary}|{due_at}|{course}|{feed.url}"
                tags = ["brightspace", "deadline"]
                if feed.audience == "llm":
                    tags.append("llm_only")
                if feed.mode == "exams_only":
                    tags.append("exam")
                tasks.append(
                    Task(
                        id=f"bspace_ical:{_stable_id(seed)}",
                        title=summary,
                        due_at=due_at,
                        source="brightspace",
                        url=str(url) if url else None,
                        course=course,
                        status="open",
                        priority=1 if due_at else 0,
                        tags=tags,
                    )
                )
        return tasks

    def _load_ical(self, feed: FeedConfig):
        _ensure_deps()
        import requests
        from icalendar import Calendar
        from urllib.parse import urlparse

        url = feed.url
        parsed = urlparse(url)
        if parsed.scheme == "file":
            path = Path(parsed.path)
            if not path.exists():
                raise FileNotFoundError(f"iCal file not found: {path}")
            payload = path.read_bytes()
            return Calendar.from_ical(payload)
        if parsed.scheme in {"http", "https"}:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            return Calendar.from_ical(resp.content)
        path = Path(url)
        if not path.exists():
            raise FileNotFoundError(f"iCal file not found: {path}")
        payload = path.read_bytes()
        return Calendar.from_ical(payload)

    def _fetch_rss_tasks(self) -> list[Task]:
        if not self.rss_feeds:
            return []
        _ensure_deps()
        import requests
        import feedparser

        tasks: list[Task] = []
        for feed in self.rss_feeds:
            resp = requests.get(feed.url, timeout=20)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                link = entry.get("link")
                published = entry.get("published") or entry.get("updated")
                due_at = None
                if published:
                    try:
                        due_at = datetime(*entry.published_parsed[:6])
                    except Exception:
                        due_at = None
                seed = entry.get("id") or f"{title}|{published}|{feed.url}"
                tags = ["brightspace", "announcement"]
                if feed.audience == "llm":
                    tags.append("llm_only")
                if feed.mode == "exams_only":
                    tags.append("exam")
                tasks.append(
                    Task(
                        id=f"bspace_rss:{_stable_id(seed)}",
                        title=title,
                        due_at=due_at,
                        source="brightspace",
                        url=link,
                        course=feed.course or feed.name or None,
                        status="open",
                        priority=0,
                        tags=tags,
                    )
                )
        return tasks
