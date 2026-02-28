from __future__ import annotations

import argparse
from pathlib import Path

from .knowledge.query import query_knowledge


def main() -> None:
    parser = argparse.ArgumentParser(description="Query Nexus Chroma knowledge store")
    parser.add_argument("--query", required=True, help="Query text")
    parser.add_argument("--db-dir", default="data/chroma", help="Chroma persist directory")
    parser.add_argument("--n-results", type=int, default=5, help="Top K results")
    parser.add_argument("--course-id", default=None, help="Optional course filter")
    parser.add_argument("--doc-type", default=None, help="Optional doc type filter")
    args = parser.parse_args()

    summary = query_knowledge(
        Path(args.db_dir),
        args.query,
        n_results=args.n_results,
        course_id=args.course_id,
        doc_type=args.doc_type,
    )

    if not summary.items:
        print("No results.")
        return

    for idx, item in enumerate(summary.items, start=1):
        score = "n/a" if item.distance is None else f"{item.distance:.4f}"
        file_name = item.metadata.get("file_name", "unknown")
        course_id = item.metadata.get("course_id", "unknown")
        print(f"[{idx}] distance={score} file={file_name} course={course_id}")
        print(item.text[:300].replace("\n", " "))
        print("-")


if __name__ == "__main__":
    main()
