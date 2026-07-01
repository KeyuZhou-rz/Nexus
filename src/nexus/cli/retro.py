"""CLI: Exam Retrospective — Nexus Phase N (Memory Layer 3)

Usage: python -m nexus.cli.retro --course-id CS202_OS --exam-date 2026-06-30
                                  [--user-input "Mutex那题很难..."] [--list] [--json]
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
        description="Post-exam retrospective analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               '  python -m nexus.cli.retro --course-id CS202_OS --exam-date 2026-06-30 '
               '--user-input "Mutex那题很难，死锁考了大题..."\n'
               "  python -m nexus.cli.retro --course-id CS202_OS --list",
    )
    parser.add_argument("--course-id", required=True, help="Course")
    parser.add_argument("--exam-date", default=None, help="Exam date (YYYY-MM-DD)")
    parser.add_argument("--user-input", default="", help="Post-exam feedback (free text)")
    parser.add_argument("--db-path", default="data/nexus.db", help="SQLite database path")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output raw JSON")
    parser.add_argument("--list", action="store_true", help="List past exams instead of generating")

    args = parser.parse_args()

    from nexus.knowledge.sqlite_store import SQLiteStore
    from nexus.knowledge.retrospective import ExamRetrospective

    store = SQLiteStore(Path(args.db_path))
    retro = ExamRetrospective(store)

    if args.list:
        exams = retro.list_exams(args.course_id)
        if args.as_json:
            print(json.dumps(exams, ensure_ascii=False, indent=2))
        else:
            print(f"\n  Past exams for {args.course_id}:")
            if exams:
                for e in exams:
                    print(f"    {e['exam_date']} — {e.get('exam_id', 'N/A')}")
            else:
                print("    (none)")
            print()
        return

    if not args.exam_date:
        print("Error: --exam-date is required (unless --list)", file=sys.stderr)
        sys.exit(1)

    user_input = args.user_input or "学生未提供考后反馈。"
    exam_id = retro.record_exam(args.course_id, args.exam_date, user_input=user_input)
    result = retro.generate_retrospective(exam_id, user_input)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if "error" in result:
            print(f"\n  Error: {result['error']}\n")
            return

        print(f"\n{'='*60}")
        print(f"  Exam Retrospective — {result.get('course_id', 'N/A')}")
        print(f"{'='*60}")
        print(f"  Exam date:     {result.get('exam_date', 'N/A')}")
        print(f"  Hit rate:      {result.get('hit_rate', 'N/A')} (predicted → actual)")

        predicted = result.get("predicted_weak", [])
        actual = result.get("actual_hits", [])
        blind = result.get("blind_spots", [])
        over = result.get("overestimated", [])

        print(f"\n  📋 Predicted Weak ({len(predicted)}):")
        for w in predicted[:10]:
            print(f"    • {w}")

        print(f"\n  ✅ Actually Appeared ({len(actual)}):")
        for a in actual:
            print(f"    • {a}")
        if not actual:
            print("    (none identified)")

        print(f"\n  👁️ Blind Spots ({len(blind)}):")
        for b in blind:
            print(f"    • {b}")
        if not blind:
            print("    (none identified)")

        print(f"\n  🤔 Overestimated ({len(over)}):")
        for o in over:
            print(f"    • {o}")
        if not over:
            print("    (none identified)")

        analysis = result.get("analysis_text", "")
        if analysis:
            print(f"\n  {'─'*50}")
            print(f"  📝 Analysis")
            print(f"  {'─'*50}")
            print(f"  {analysis}")

        hook = result.get("retention_hook", "")
        if hook:
            print(f"\n  🧠 Memory Hook: {hook}")

        print()


if __name__ == "__main__":
    main()
