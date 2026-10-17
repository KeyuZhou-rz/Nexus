"""``nexus`` / ``nexus next``: the one task to do now, in the terminal."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core.academic_state import display_course
from ..core.academic_state_service import AcademicStateService
from ..core.action_service import ActionService
from ..core.config import default_config
from ..core.context_api import persist_next_action
from ..core.dashboard.copy import hours_copy, local_due, when_copy
from ..core.storage import load_feeds
from ..core.sync_service import load_sync_receipt, run_sync
from ..core.v1_config import V1_TZ

OPEN_STATUSES = {"open", "in_progress", "overdue", "missing"}
STALE_AFTER = timedelta(hours=2)
MORE_LIMIT = 3


def _data_dir(value: Path | None) -> Path:
    return Path(value or default_config().data_dir).expanduser()


def open_rows(data_dir: Path) -> list[dict]:
    if not (data_dir / "nexus.db").is_file():
        return []
    rows = AcademicStateService(data_dir).store.list_obligations()
    return sorted(
        (row for row in rows if row["status"] in OPEN_STATUSES),
        key=lambda row: (
            row["status"] == "missing",
            local_due(row) or datetime.max.replace(tzinfo=V1_TZ),
            row["id"],
        ),
    )


def _google_authorized(data_dir: Path) -> bool:
    return (data_dir / "google_client_secret.json").is_file() and (data_dir / "google_token.json").is_file()


def needs_sync(data_dir: Path, *, now: datetime | None = None) -> bool:
    """True when a source is configured and the last sync is old or predates feeds.json."""
    feeds_path = data_dir / "feeds.json"
    if not load_feeds(feeds_path) and not _google_authorized(data_dir):
        return False
    finished = load_sync_receipt(data_dir).get("finished_at")
    try:
        last = datetime.fromisoformat(finished) if finished else None
    except ValueError:
        last = None
    if last is None:
        return True
    current = now or datetime.now(timezone.utc)
    if current - last > STALE_AFTER:
        return True
    return feeds_path.is_file() and feeds_path.stat().st_mtime > last.timestamp()


def render(data_dir: Path, payload: dict, *, now: datetime | None = None) -> str:
    current = (now or datetime.now(timezone.utc)).astimezone(V1_TZ)
    rows = open_rows(data_dir)
    running = ActionService(data_dir).running_action()
    focus_id = running["obligation_id"] if running else payload.get("obligation_id")
    focus = next((row for row in rows if row["id"] == focus_id), None)
    lines: list[str] = []

    if focus is None or not payload.get("available") and not running:
        lines.append("现在没有要做的事。")
        if not load_feeds(data_dir / "feeds.json"):
            lines.append('接入课程：nexus init --feed-url "<Brightspace 日历订阅链接>"')
        lines.append('加一件：nexus add "任务" --due 10-20')
        return "\n".join(lines)

    course = display_course(focus.get("course")) or "自己的任务"
    detail = [course, when_copy(focus, current)]
    hours = hours_copy(focus.get("remaining_hours"), None)
    if hours:
        detail.append(hours)
    if running:
        minutes = int(running.get("elapsed_minutes") or 0)
        lines.append(f"正在做  {focus['title']}（已 {minutes} 分钟）")
    else:
        lines.append(f"现在做  {focus['title']}")
    lines.append("        " + " · ".join(detail))
    if not running and payload.get("suggestion"):
        lines.append("        " + payload["suggestion"])

    more = [row for row in rows if row["id"] != focus["id"]][:MORE_LIMIT]
    if more:
        lines.append("")
        for index, row in enumerate(more):
            label = "之后  " if index == 0 else "      "
            other = display_course(row.get("course")) or "自己的任务"
            lines.append(f"{label}  {row['title']} · {other} · {when_copy(row, current)}")

    lines.append("")
    lines.append(
        "nexus pause 暂停 · nexus done 做完了" if running
        else "nexus start 开始计时 · nexus done 做完了"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the one task to do now")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the Next Action JSON")
    args = parser.parse_args(argv)

    data_dir = _data_dir(args.data_dir)
    payload = persist_next_action(data_dir=data_dir)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(data_dir, payload))
    return 0


def home(argv: list[str] | None = None) -> int:
    """Bare ``nexus``: refresh stale sources, then show the one task to do now."""
    parser = argparse.ArgumentParser(prog="nexus", description="Show the one task to do now")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--offline", action="store_true", help="Skip the automatic source refresh")
    args = parser.parse_args(argv)

    data_dir = _data_dir(args.data_dir)
    if not args.offline and needs_sync(data_dir):
        receipt = run_sync(data_dir=data_dir, trigger="cli", include_google=_google_authorized(data_dir))
        if receipt.get("status") == "failed":
            print("（有课程来源没更新成功，先用上次的清单；详情：nexus sync-status）", file=sys.stderr)
    print(render(data_dir, persist_next_action(data_dir=data_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
