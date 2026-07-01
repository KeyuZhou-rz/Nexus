"""CLI: Knowledge Gap Scanner — Nexus Phase N (Memory Layer 1)

Usage: python -m nexus.cli.scan --course-id CS202_OS [--db-path data/nexus.db] [--json]
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
        description="Weekly knowledge gap scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n"
               "  python -m nexus.cli.scan --course-id CS202_OS\n"
               "  python -m nexus.cli.scan --course-id CS202_OS --json",
    )
    parser.add_argument("--course-id", required=True, help="Course to scan")
    parser.add_argument("--db-path", default="data/nexus.db", help="SQLite database path")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output raw JSON")

    args = parser.parse_args()

    from nexus.knowledge.sqlite_store import SQLiteStore
    from nexus.knowledge.gap_scanner import GapScanner

    store = SQLiteStore(Path(args.db_path))
    scanner = GapScanner(store)
    result = scanner.run_weekly(args.course_id)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        cp = result["checkpoint"]
        ranking = result["weakness_ranking"]

        print(f"\n{'='*60}")
        print(f"  Knowledge Gap Scan — {cp.get('checkpoint_id', 'N/A')}")
        print(f"{'='*60}")
        print(f"  Concepts extracted:  {cp.get('concepts_extracted', 0)}")
        print(f"  Matched to existing: {cp.get('concepts_matched', 0)}")
        if cp.get("error"):
            print(f"  ⚠ {cp['error']}")
        print()

        if ranking:
            print(f"  Weakness Ranking (top {min(15, len(ranking))}):")
            print(f"  {'Rank':<5} {'Concept':<40} {'Importance':<12} {'Mastery':<8} {'Score':<8}")
            print(f"  {'-'*5} {'-'*40} {'-'*12} {'-'*8} {'-'*8}")
            for i, r in enumerate(ranking[:15], 1):
                mastery_pct = f"{r['mastery']*100:.0f}%"
                print(
                    f"  {i:<5} {r['name'][:38]:<40} "
                    f"{r['importance']}/10{'':<6} "
                    f"{mastery_pct:<8} "
                    f"{r['weakness_score']:.3f}"
                )
        else:
            print("  No weakness data available.")
        print()


if __name__ == "__main__":
    main()
