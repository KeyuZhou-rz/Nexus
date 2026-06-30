"""
nexus concepts-backfill CLI — Phase M1-2

从 ChromaDB 回填 chunks 到 SQLite，为后续概念图谱填充铺路。

用法：
  PYTHONPATH=src python -m nexus.cli.concepts_backfill
  PYTHONPATH=src python -m nexus.cli.concepts_backfill --course-id CS202_OS
  PYTHONPATH=src python -m nexus.cli.concepts_backfill --db-dir data/chroma --sqlite-path data/nexus.db
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ChromaDB → SQLite chunk backfill (Phase M1-2)",
    )
    parser.add_argument(
        "--db-dir",
        default="data/chroma",
        help="ChromaDB 持久化目录 (default: data/chroma)",
    )
    parser.add_argument(
        "--sqlite-path",
        default="data/nexus.db",
        help="SQLite 文件路径 (default: data/nexus.db)",
    )
    parser.add_argument(
        "--course-id",
        default=None,
        help="限定回填单个课程（collection name），不指定则全部课程",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    from nexus.knowledge.chunk_backfill import ChunkBackfill
    from nexus.knowledge.sqlite_store import SQLiteStore

    db_dir = Path(args.db_dir)
    sqlite_path = Path(args.sqlite_path)

    if not db_dir.exists():
        logger.error("ChromaDB directory not found: %s", db_dir)
        return

    store = SQLiteStore(sqlite_path)
    backfill = ChunkBackfill(db_dir, store)

    stats = backfill.run(course_id=args.course_id)

    total_chunks = sum(s["chunks"] for s in stats.values())
    total_docs = sum(s["docs"] for s in stats.values())

    print(f"Collections processed: {len(stats)}")
    for name, s in stats.items():
        print(f"  {name}: {s['chunks']} chunks, {s['docs']} documents")
    print(f"Total: {total_chunks} chunks, {total_docs} documents")


if __name__ == "__main__":
    main()
