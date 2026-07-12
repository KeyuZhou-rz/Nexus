"""Read-only SQLite/Chroma consistency audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit SQLite and Chroma knowledge indexes (read-only)")
    parser.add_argument("--sqlite-path", default="data/nexus.db")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from nexus.knowledge.chunk_backfill import KnowledgeConsistencyAudit

    report = KnowledgeConsistencyAudit(Path(args.sqlite_path), Path(args.db_dir)).run()
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report.get("summary", {})
        print(f"Consistency: {'OK' if report.get('ok') else 'FAILED'}")
        if summary:
            print("  " + ", ".join(f"{key}={value}" for key, value in summary.items()))
        for issue in report.get("issues", []):
            print(f"  [{issue['code']}] {issue['message']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
