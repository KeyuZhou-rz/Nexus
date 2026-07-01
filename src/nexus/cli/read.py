from __future__ import annotations

import argparse
from pathlib import Path

from ..knowledge.read import read_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="Read chunks from Nexus knowledge store")
    parser.add_argument("--db-dir", default="data/chroma", help="Chroma persist directory")
    parser.add_argument("--course-id", default=None, help="Optional course filter")
    parser.add_argument("--doc-type", default=None, help="Optional doc type filter")
    parser.add_argument("--source-path", default=None, help="Optional source file path filter")
    parser.add_argument("--session-id", default=None, help="Optional session filter")
    parser.add_argument("--collection", default="nexus_chunks", help="Collection name")
    parser.add_argument("--limit", type=int, default=20, help="Max chunks to read")
    args = parser.parse_args()

    summary = read_chunks(
        Path(args.db_dir),
        limit=args.limit,
        course_id=args.course_id,
        doc_type=args.doc_type,
        source_path=args.source_path,
        session_id=args.session_id,
        collection_name=args.collection,
    )

    if not summary.items:
        print("No chunks found.")
        return

    for idx, item in enumerate(summary.items, start=1):
        file_name = item.metadata.get("file_name", "unknown")
        chunk_index = item.metadata.get("chunk_index", "0")
        course = item.metadata.get("course_id", "unknown")
        print(f"[{idx}] file={file_name} chunk={chunk_index} course={course}")
        print(item.text[:500].replace("\n", " "))
        print("-")


if __name__ == "__main__":
    main()
