"""
nexus concepts-backfill CLI — Phase M1-2 + M1-4

从 ChromaDB 回填 chunks 到 SQLite + 可选概念提取。

用法：
  # 仅回填 chunks
  PYTHONPATH=src python -m nexus.cli.concepts_backfill

  # 回填 + 概念提取（Pass 1 + LLM Pass 2）
  PYTHONPATH=src python -m nexus.cli.concepts_backfill --extract-concepts

  # 限定课程
  PYTHONPATH=src python -m nexus.cli.concepts_backfill --course-id CS202_OS --extract-concepts

  # 指定路径
  PYTHONPATH=src python -m nexus.cli.concepts_backfill --db-dir data/chroma --sqlite-path data/nexus.db
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _load_qwen_key() -> str | None:
    """从标准位置加载 QWEN API key。"""
    # 优先 env
    key = os.environ.get("QWEN_API_KEY")
    if key:
        return key
    # 次选 key file
    key_file = Path("data/QWEN_API_KEY.json")
    if key_file.exists():
        try:
            data = json.loads(key_file.read_text())
            return data.get("QWEN_API_KEY") or data.get("api_key") or data.get("key")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ChromaDB → SQLite chunk backfill + concept extraction (Phase M1)",
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
        help="限定单个课程（collection name），不指定则全部课程",
    )
    parser.add_argument(
        "--extract-concepts",
        action="store_true",
        default=False,
        help="回填后运行概念提取管线 (Pass 1 + LLM Pass 2/3 if key available)",
    )
    parser.add_argument(
        "--llm-api-key",
        default=None,
        help="LLM API key（默认读 QWEN_API_KEY env 或 data/QWEN_API_KEY.json）",
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

    # ── M1-2: 回填 chunks ──
    stats = backfill.run(course_id=args.course_id)

    total_chunks = sum(s["chunks"] for s in stats.values())
    total_docs = sum(s["docs"] for s in stats.values())

    print(f"Backfill: {len(stats)} collections")
    for name, s in stats.items():
        print(f"  {name}: {s['chunks']} chunks, {s['docs']} documents")
    print(f"  Total: {total_chunks} chunks, {total_docs} documents")

    # ── M1-4: 概念提取 ──
    if args.extract_concepts:
        llm_key = args.llm_api_key or _load_qwen_key()
        if llm_key:
            logger.info("LLM key found — running Pass 2/3 refinement")
        else:
            logger.info("No LLM key — running Pass 1 only (rule-based)")

        print()
        for name in stats:
            course_id = name  # collection name = course_id
            extraction_stats = backfill.run_extraction(course_id, llm_api_key=llm_key)
            if extraction_stats:
                print(
                    f"  {course_id}: {extraction_stats['concepts']} concepts, "
                    f"{extraction_stats['relations']} relations"
                )
            else:
                print(f"  {course_id}: no chunks — skipped")


if __name__ == "__main__":
    main()
