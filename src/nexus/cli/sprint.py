"""CLI: Exam Sprint Planner — Nexus Phase N (Memory Layer 2)

Usage: python -m nexus.cli.sprint --course-id CS202_OS --exam-date 2026-07-15
                                  [--chapter-weights '{"ch3":0.15,"ch7":0.20}']
                                  [--daily-hours 2.0] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent.parent  # src/
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-exam sprint planner (14-day window)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n"
               "  python -m nexus.cli.sprint --course-id CS202_OS --exam-date 2026-07-15\n"
               '  python -m nexus.cli.sprint --course-id CS202_OS --exam-date 2026-07-15 '
               '--chapter-weights \'{"ch3":0.15,"ch7":0.20,"ch9":0.30}\'',
    )
    parser.add_argument("--course-id", required=True, help="Course to plan for")
    parser.add_argument("--exam-date", required=True, help="Exam date (YYYY-MM-DD)")
    parser.add_argument("--chapter-weights", default=None, help="Chapter weights as JSON dict")
    parser.add_argument("--daily-hours", type=float, default=2.0, help="Daily study hours (default: 2.0)")
    parser.add_argument("--db-path", default="data/nexus.db", help="SQLite database path")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output raw JSON")

    args = parser.parse_args()

    from nexus.knowledge.sqlite_store import SQLiteStore
    from nexus.knowledge.sprint_planner import SprintPlanner

    # Parse chapter weights
    chapter_weights = None
    if args.chapter_weights:
        try:
            chapter_weights = json.loads(args.chapter_weights)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON for --chapter-weights: {args.chapter_weights}", file=sys.stderr)
            sys.exit(1)

    store = SQLiteStore(Path(args.db_path))
    planner = SprintPlanner(store)
    result = planner.generate(
        course_id=args.course_id,
        exam_date=args.exam_date,
        chapter_weights=chapter_weights,
        daily_hours=args.daily_hours,
    )

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = result.get("mode", "unknown")
        days = result.get("days_until_exam", "?")

        print(f"\n{'='*60}")
        print(f"  Exam Sprint Plan — {result.get('course_id', 'N/A')}")
        print(f"{'='*60}")
        print(f"  Exam date:    {result.get('exam_date', 'N/A')}")
        print(f"  Days left:    {days}")
        print(f"  Mode:         {mode}")
        print(f"  Method:       {result.get('method', 'N/A')}")

        if mode == "relaxed":
            print(f"\n  {result.get('message', '')}")
            print()
            return

        if mode == "error":
            print(f"\n  Error: {result.get('message', '')}")
            print()
            return

        plan = result.get("plan", [])
        if plan:
            print(f"\n  {'─'*50}")
            for day_entry in plan:
                d = day_entry["day"]
                date = day_entry["date"]
                focus = day_entry.get("focus", [])
                rationale = day_entry.get("rationale", "")
                challenge = day_entry.get("challenge", "")
                mins = day_entry.get("estimated_minutes", 0)
                print(f"\n  Day {d} — {date} ({mins} min)")
                print(f"  Focus: {', '.join(focus[:5]) if focus else 'Free review'}")
                print(f"  {rationale}")
                if challenge:
                    print(f"  💬 {challenge}")
        else:
            print("\n  No plan generated.")

        print()


if __name__ == "__main__":
    main()
