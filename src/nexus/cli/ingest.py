"""
nexus ingest CLI — Phase 3 版本

默认使用 CourseIngestor（Gemini Vision + 双写 ChromaDB+SQLite）。
传入 --legacy 回退到 Phase 2 的纯文本切片路径。

用法示例：
  # Phase 3（推荐）：LLM 语义解析，自动读取 GEMINI_API_KEY
  python -m nexus.ingest_cli --input slides/ --course-id CS202_OS

  # 强制重新解析（忽略 hash 缓存）
  python -m nexus.ingest_cli --input Lecture07.pdf --course-id CS202_OS --force

  # Legacy 模式（固定大小文本切片，不走 LLM）
  python -m nexus.ingest_cli --input notes.md --course-id CS202_OS --legacy
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# 支持的文件扩展名
PHASE3_EXTENSIONS = (".pdf", ".pptx", ".ppt", ".docx", ".doc")
LEGACY_EXTENSIONS = (".md", ".txt", ".markdown", ".docx", ".pdf")


def _collect_files(input_path: Path, extensions: tuple[str, ...]) -> list[Path]:
    """收集输入路径下所有指定扩展名的文件（文件夹递归，单文件直接返回）。"""
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in extensions else []
    if input_path.is_dir():
        return sorted(
            p for p in input_path.rglob("*")
            if p.is_file() and p.suffix.lower() in extensions
        )
    return []


def _run_phase3(args: argparse.Namespace) -> None:
    """Phase 3 ingest 路径：CourseIngestor（Gemini Vision + 双写）。"""
    from ..knowledge.ingestor import CourseIngestor

    files = _collect_files(Path(args.input), PHASE3_EXTENSIONS)
    if not files:
        raise SystemExit(f"未找到支持的文件（{PHASE3_EXTENSIONS}）: {args.input}")

    db_dir = Path(args.db_dir)
    sqlite_path = Path(args.sqlite_path)

    ingestor = CourseIngestor(
        db_dir=db_dir,
        sqlite_path=sqlite_path,
    )

    total_chunks = 0
    for fp in files:
        result = ingestor.ingest_file(fp, args.course_id, force=args.force)
        print(result)
        total_chunks += result.chunks

    print(f"\n完成：共 {len(files)} 个文件，{total_chunks} chunks")


def _run_legacy(args: argparse.Namespace) -> None:
    """Legacy ingest 路径：固定大小文本切片（Phase 2）。"""
    from ..knowledge.document_text import SUPPORTED_TEXT_SUFFIXES
    from ..knowledge.ingest import ingest_files
    from ..knowledge.store import ChromaKnowledgeStore

    files = _collect_files(Path(args.input), tuple(SUPPORTED_TEXT_SUFFIXES))
    if not files:
        raise SystemExit(f"No supported files found at: {args.input}")

    store = ChromaKnowledgeStore(Path(args.db_dir))
    summary = ingest_files(
        files,
        store,
        course_id=args.course_id,
        doc_type=getattr(args, "doc_type", "notes"),
        max_chars=getattr(args, "max_chars", 900),
        overlap=getattr(args, "overlap", 120),
    )
    print(f"Ingested files: {summary.files}, chunks: {summary.chunks}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nexus ingest — 将课件解析并存入知识库"
    )
    parser.add_argument("--input", required=True,
                        help="输入文件或目录路径")
    parser.add_argument("--course-id", required=True,
                        help="课程代码，如 CS202_OS（用作 ChromaDB collection 名称）")
    parser.add_argument("--db-dir", default="data/chroma",
                        help="ChromaDB 持久化目录（默认 data/chroma）")
    parser.add_argument("--sqlite-path", default="data/nexus.db",
                        help="SQLite 数据库路径（默认 data/nexus.db）")
    parser.add_argument("--force", action="store_true",
                        help="忽略 hash 缓存，强制重新解析所有文件")
    parser.add_argument("--legacy", action="store_true",
                        help="使用 Phase 2 legacy 模式（固定大小文本切片，不走 LLM）")

    # Legacy 专有参数
    parser.add_argument("--doc-type", default="notes",
                        help="[legacy] 文档类型 metadata")
    parser.add_argument("--max-chars", type=int, default=900,
                        help="[legacy] chunk 最大字符数")
    parser.add_argument("--overlap", type=int, default=120,
                        help="[legacy] chunk 重叠字符数")

    args = parser.parse_args()

    if args.legacy:
        _run_legacy(args)
    else:
        _run_phase3(args)


if __name__ == "__main__":
    main()
