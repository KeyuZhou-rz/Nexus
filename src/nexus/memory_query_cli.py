from __future__ import annotations

import argparse
from pathlib import Path

from .knowledge.query import query_knowledge


def main() -> None:
    parser = argparse.ArgumentParser(description="Query conversation memory evidence")
    parser.add_argument("--query", required=True)
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--collection", default="nexus_memory_evidence")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--n-results", type=int, default=5)
    args = parser.parse_args()

    where_session = args.session_id or None
    summary = query_knowledge(
        Path(args.db_dir),
        query_text=args.query,
        n_results=args.n_results,
        course_id=None,
        doc_type="conversation_memory",
        collection_name=args.collection,
        session_id=where_session,
    )

    if not summary.items:
        print("No memory evidence found.")
        return

    for idx, item in enumerate(summary.items, start=1):
        dist = "n/a" if item.distance is None else f"{item.distance:.4f}"
        print(f"[{idx}] dist={dist} topic={item.metadata.get('topic', 'unknown')}")
        print(item.text)
        print("-")


if __name__ == "__main__":
    main()
