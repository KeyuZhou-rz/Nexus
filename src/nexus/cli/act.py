"""Terminal feedback on the same obligations the dashboard shows.

``nexus start|pause|done [关键词]`` act on the running task, else the current
recommendation, else the one open task whose title or course matches.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path

from ..core.academic_state import display_course
from ..core.academic_state_service import AcademicStateService
from ..core.action_service import ActionService
from ..core.context_api import persist_next_action
from ..core.dashboard.copy import when_copy
from ..core.v1_config import V1_TZ
from .next_action import _data_dir, open_rows, render


def _parser(verb: str, help_text: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"nexus {verb}", description=help_text)
    parser.add_argument("query", nargs="?", help="Part of a task title or course; defaults to the current task")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    return parser


def _target(data_dir: Path, query: str | None) -> dict | None:
    rows = open_rows(data_dir)
    if query:
        needle = query.casefold()
        matches = [
            row for row in rows
            if needle in row["title"].casefold()
            or needle in display_course(row.get("course")).casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            print(f"没有找到包含「{query}」的未完成任务。", file=sys.stderr)
        else:
            print(f"「{query}」对应 {len(matches)} 件任务，再具体一点：", file=sys.stderr)
            for row in matches:
                print(f"  {row['title']} · {display_course(row.get('course')) or '自己的任务'}", file=sys.stderr)
        return None
    running = ActionService(data_dir).running_action()
    oid = running["obligation_id"] if running else persist_next_action(data_dir=data_dir).get("obligation_id")
    row = next((row for row in rows if row["id"] == oid), None)
    if row is None:
        print("现在没有要做的任务。", file=sys.stderr)
    return row


def _finish(data_dir: Path, message: str) -> int:
    print(message)
    print()
    print(render(data_dir, persist_next_action(data_dir=data_dir)))
    return 0


def start(argv: list[str] | None = None) -> int:
    args = _parser("start", "Start timing a task").parse_args(argv)
    data_dir = _data_dir(args.data_dir)
    row = _target(data_dir, args.query)
    if row is None:
        return 1
    actions = ActionService(data_dir)
    running = actions.running_action()
    if running and running["obligation_id"] == row["id"]:
        return _finish(data_dir, f"「{row['title']}」已经在计时。")
    if running:
        print(f"「{running['title']}」正在做，先 nexus pause。", file=sys.stderr)
        return 1
    actions.start(row["id"])
    return _finish(data_dir, f"开始「{row['title']}」。")


def pause(argv: list[str] | None = None) -> int:
    args = _parser("pause", "Pause the running task").parse_args(argv)
    data_dir = _data_dir(args.data_dir)
    actions = ActionService(data_dir)
    running = actions.running_action()
    if running is None:
        print("现在没有在计时的任务。", file=sys.stderr)
        return 1
    actions.stop(running["obligation_id"])
    minutes = int(running.get("elapsed_minutes") or 0)
    return _finish(data_dir, f"暂停「{running['title']}」，这次做了 {minutes} 分钟。")


def done(argv: list[str] | None = None) -> int:
    args = _parser("done", "Mark a task complete").parse_args(argv)
    data_dir = _data_dir(args.data_dir)
    row = _target(data_dir, args.query)
    if row is None:
        return 1
    ActionService(data_dir).complete(row["id"])
    return _finish(data_dir, f"完成「{row['title']}」。")


def _parse_due(value: str, now: datetime) -> datetime:
    date_part, _, time_part = value.strip().partition(" ")
    pieces = date_part.split("-")
    if len(pieces) == 2:
        pieces = [str(now.year), *pieces]
    year, month, day = (int(piece) for piece in pieces)
    clock = time.fromisoformat(time_part) if time_part else time(23, 59)
    due = datetime(year, month, day, clock.hour, clock.minute, tzinfo=V1_TZ)
    if len(date_part.split("-")) == 2 and due.date() < now.date():
        due = due.replace(year=year + 1)
    return due


def add(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus add", description="Add a task the calendar does not have")
    parser.add_argument("title")
    parser.add_argument("--due", help='Deadline: 2026-10-20, 10-20, or "10-20 18:00" (default 23:59)')
    parser.add_argument("--course", help="Course or category")
    parser.add_argument("--hours", type=float, help="Estimated hours of work")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    args = parser.parse_args(argv)

    data_dir = _data_dir(args.data_dir)
    now = datetime.now(V1_TZ)
    try:
        due = _parse_due(args.due, now) if args.due else None
    except ValueError:
        parser.error(f"cannot read --due {args.due!r}; use 2026-10-20, 10-20, or \"10-20 18:00\"")
    try:
        row = AcademicStateService(data_dir).add_manual_obligation(
            title=args.title, course=args.course, due_at=due,
            estimated_hours=args.hours, remaining_hours=args.hours,
        )
    except ValueError:
        parser.error("title is required")
    return _finish(data_dir, f"加上了「{row['title']}」· {when_copy(row, now)}。")
