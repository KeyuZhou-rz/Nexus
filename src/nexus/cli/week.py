"""``nexus week``: seven days of self-use numbers from local state."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..core.week import build_week
from .next_action import _data_dir


def _percent(part: int, whole: int) -> str:
    return f"（{round(part * 100 / whole)}%）" if whole else ""


def render(report: dict) -> str:
    since = datetime.fromisoformat(report["since"])
    until = datetime.fromisoformat(report["until"])
    lines = [f"最近 {report['days']} 天（{since:%m 月 %d 日} – {until:%m 月 %d 日}）"]
    if not report["days_used"]:
        lines.append("还没有记录。敲 nexus 看推荐，做的时候 nexus start，做完 nexus done。")
        return "\n".join(lines)
    lines.append(f"用了    {report['days_used']}/{report['days']} 天")
    lines.append(
        f"推荐    {report['shown']} 件，{report['followed']} 件随后开始或完成"
        f"{_percent(report['followed'], report['shown'])}"
    )
    done = f"完成    {report['completed']} 件，{report['completed_recommended']} 件是推荐过的"
    if report["completed_without_timer"]:
        done += f"；{report['completed_without_timer']} 件没计时，用时没学到"
    lines.append(done)
    lines.append(f"补录    {report['manual_added']} 件日历上没有的")
    if report["measured"]:
        lines.append(
            f"用时    实测 {report['measured']} 件：估计 {report['estimated_hours']:g} 小时，"
            f"实际 {report['actual_hours']:g} 小时"
        )
    else:
        lines.append("用时    还没有实测；用 nexus start / pause 计时后才有")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus week", description="Show seven days of self-use numbers")
    parser.add_argument("--data-dir", type=Path, help="Override the runtime data directory")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit the report as JSON")
    args = parser.parse_args(argv)

    report = build_week(_data_dir(args.data_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.as_json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
