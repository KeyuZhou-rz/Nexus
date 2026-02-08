from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .aggregators.brightspace import BrightspaceAggregator, FeedConfig
from .aggregators.google import GoogleAggregator
from .models import FeedSource, Task
from .storage import load_feeds, load_tasks, save_tasks


@dataclass
class AggregationResult:
    tasks: list[Task]
    errors: list[str]


@dataclass
class FeedStatus:
    name: str
    kind: str
    url: str
    enabled: bool
    ok: bool
    item_count: int
    error: str | None = None


def _dedupe(tasks: Iterable[Task]) -> list[Task]:
    seen: dict[str, Task] = {}
    for task in tasks:
        key = task.id or f"{task.source}:{task.title}:{task.due_at}"
        if key not in seen:
            seen[key] = task
    return list(seen.values())


def _split_feeds(feeds: list[FeedSource]) -> tuple[list[FeedConfig], list[FeedConfig]]:
    ical_feeds: list[FeedConfig] = []
    rss_feeds: list[FeedConfig] = []
    for feed in feeds:
        if not feed.enabled:
            continue
        if feed.kind in {"brightspace_ical", "ical_file"}:
            ical_feeds.append(
                FeedConfig(
                    name=feed.name,
                    url=feed.url,
                    course=feed.course,
                    audience=feed.audience,
                    mode=feed.mode,
                )
            )
        elif feed.kind == "brightspace_rss":
            rss_feeds.append(
                FeedConfig(
                    name=feed.name,
                    url=feed.url,
                    course=feed.course,
                    audience=feed.audience,
                    mode=feed.mode,
                )
            )
    return ical_feeds, rss_feeds


def run_aggregation(include_google: bool = True) -> AggregationResult:
    tasks: list[Task] = []
    errors: list[str] = []

    feeds = load_feeds()
    ical_feeds, rss_feeds = _split_feeds(feeds)
    if ical_feeds or rss_feeds:
        try:
            tasks.extend(BrightspaceAggregator(ical_feeds, rss_feeds).fetch_tasks())
        except Exception as exc:
            errors.append(f"Brightspace error: {exc}")

    if include_google:
        try:
            tasks.extend(GoogleAggregator().fetch_tasks())
        except Exception as exc:
            errors.append(f"Google error: {exc}")

    tasks = _dedupe(tasks)
    save_tasks(tasks)
    return AggregationResult(tasks=tasks, errors=errors)


def sync_google_calendar(existing_tasks: list[Task] | None = None) -> AggregationResult:
    tasks = list(existing_tasks) if existing_tasks is not None else load_tasks()
    errors: list[str] = []
    try:
        calendar_tasks = GoogleAggregator(include_gmail=False, include_calendar=True).fetch_tasks()
        tasks = [task for task in tasks if task.source != "gcal"]
        tasks.extend(calendar_tasks)
    except Exception as exc:
        errors.append(f"Google Calendar sync error: {exc}")
    tasks = _dedupe(tasks)
    save_tasks(tasks)
    return AggregationResult(tasks=tasks, errors=errors)


def check_brightspace_feeds() -> list[FeedStatus]:
    feeds = load_feeds()
    statuses: list[FeedStatus] = []
    for feed in feeds:
        if feed.kind not in {"brightspace_ical", "brightspace_rss", "ical_file"}:
            continue
        if not feed.enabled:
            statuses.append(
                FeedStatus(
                    name=feed.course or feed.name,
                    kind=feed.kind,
                    url=feed.url,
                    enabled=False,
                    ok=False,
                    item_count=0,
                    error="disabled",
                )
            )
            continue
        try:
            if feed.kind == "brightspace_rss":
                tasks = BrightspaceAggregator(
                    [],
                    [
                        FeedConfig(
                            feed.name,
                            feed.url,
                            course=feed.course,
                            audience=feed.audience,
                            mode=feed.mode,
                        )
                    ],
                ).fetch_tasks()
            else:
                tasks = BrightspaceAggregator(
                    [
                        FeedConfig(
                            feed.name,
                            feed.url,
                            course=feed.course,
                            audience=feed.audience,
                            mode=feed.mode,
                        )
                    ],
                    [],
                ).fetch_tasks()
            statuses.append(
                FeedStatus(
                    name=feed.course or feed.name,
                    kind=feed.kind,
                    url=feed.url,
                    enabled=True,
                    ok=True,
                    item_count=len(tasks),
                )
            )
        except Exception as exc:
            statuses.append(
                FeedStatus(
                    name=feed.course or feed.name,
                    kind=feed.kind,
                    url=feed.url,
                    enabled=True,
                    ok=False,
                    item_count=0,
                    error=str(exc),
                )
            )
    return statuses
