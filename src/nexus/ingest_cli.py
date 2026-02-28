from __future__ import annotations

import argparse
from pathlib import Path

from .knowledge.ingest import ingest_markdown_files
from .knowledge.store import ChromaKnowledgeStore


def _collect_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(
            [p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
        )
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest markdown/text files into Nexus Chroma store")
    parser.add_argument("--input", required=True, help="Input file or directory (.md/.txt)")
    parser.add_argument("--course-id", required=True, help="Course id metadata")
    parser.add_argument("--doc-type", default="notes", help="Document type metadata")
    parser.add_argument("--db-dir", default="data/chroma", help="Chroma persist directory")
    parser.add_argument("--max-chars", type=int, default=900, help="Chunk max chars")
    parser.add_argument("--overlap", type=int, default=120, help="Chunk overlap")
    args = parser.parse_args()

    input_path = Path(args.input)
    files = _collect_files(input_path)
    if not files:
        raise SystemExit(f"No markdown/text files found at: {input_path}")

    store = ChromaKnowledgeStore(Path(args.db_dir))
    summary = ingest_markdown_files(
        files,
        store,
        course_id=args.course_id,
        doc_type=args.doc_type,
        max_chars=args.max_chars,
        overlap=args.overlap,
    )
    print(f"Ingested files: {summary.files}, chunks: {summary.chunks}")


if __name__ == "__main__":
    main()
