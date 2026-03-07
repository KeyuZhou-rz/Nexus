"""
完整 Ingest Pipeline — Nexus Phase 3

功能：
- 幂等性：用 MD5 file_hash 检测文件是否已解析，跳过或增量更新
- 双写：解析结果同时写入 ChromaDB（向量检索）和 SQLite（结构化元数据）
- 课程隔离：每门课使用独立的 ChromaDB collection
- Lecture 编号推断：从文件名 pattern 解析 lecture 编号（如 "Lecture07.pdf"→7）

使用示例：
    from pathlib import Path
    from nexus.knowledge.ingestor import CourseIngestor

    ingestor = CourseIngestor(
        db_dir=Path("data/chroma"),
        sqlite_path=Path("data/nexus.db"),
    )
    result = ingestor.ingest_file(
        Path("slides/Lecture07_Deadlock.pdf"),
        course_id="CS202_OS",
    )
    print(result)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .parser import parse_document, ParsedChunk
from .store import ChromaKnowledgeStore
from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────

def _md5(filepath: Path) -> str:
    """计算文件 MD5，用于幂等性检测（文件内容变化时重新解析）。"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _infer_lecture_number(filename: str) -> int | None:
    """
    从文件名推断 lecture 编号。
    支持格式：Lecture07、lecture_3、lec02、Week3、Chapter12 等。
    推断不到时返回 None。
    """
    patterns = [
        r"(?:lecture|lec|week|chapter|ch|unit)[\s_-]*(\d+)",  # Lecture07, lec_2
        r"_(\d{2,3})(?:_|\.)",                                 # slides_07.pdf
        r"^(\d+)[_\s]",                                        # 07_deadlock.pdf
    ]
    name_lower = filename.lower()
    for pat in patterns:
        m = re.search(pat, name_lower)
        if m:
            return int(m.group(1))
    return None


def _chunk_id(course_id: str, filename: str, idx: int) -> str:
    """
    生成 chunk ID，格式：{course_id}_{filename_stem}_{idx:03d}
    与 ChromaDB document id 和 SQLite chunk_id 保持一致，方便交叉查询。
    """
    stem = Path(filename).stem[:40]          # 截断避免 ID 过长
    safe_course = re.sub(r"[^a-zA-Z0-9_-]", "_", course_id)
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", stem)
    return f"{safe_course}_{safe_stem}_{idx:03d}"


def _chunk_to_metadata(
    chunk: ParsedChunk,
    course_id: str,
    filename: str,
    lecture_number: int | None,
) -> dict[str, str | int]:
    """
    将 ParsedChunk 转为 ChromaDB metadata 字典。
    ChromaDB 不支持 list 类型，keywords/prerequisites 转为逗号分隔字符串。
    """
    return {
        "course_id": course_id,
        "source_file": filename,
        "topic": chunk.topic,
        "type": chunk.type or "overview",
        # list → 逗号分隔字符串（ChromaDB 限制）
        "keywords": ",".join(chunk.keywords),
        "prerequisites": ",".join(chunk.prerequisites),
        "page": chunk.page or 0,
        "lecture_number": lecture_number or 0,
    }


# ────────────────────────────────────────────────
# 结果数据类
# ────────────────────────────────────────────────

@dataclass
class IngestResult:
    """单次文件 ingest 的结果摘要。"""
    filepath: Path
    course_id: str
    status: str                            # "skipped" | "ingested" | "failed"
    chunks: int = 0
    parse_method: str = ""                 # "gemini_vision" | "text_fallback"
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def __str__(self) -> str:
        if self.status == "skipped":
            return f"[SKIP] {self.filepath.name} — 文件未变更，跳过"
        if self.status == "failed":
            return f"[FAIL] {self.filepath.name} — {self.error}"
        return (
            f"[OK]   {self.filepath.name} — "
            f"{self.chunks} chunks via {self.parse_method}"
            + (f" | ⚠ {len(self.warnings)} warnings" if self.warnings else "")
        )


# ────────────────────────────────────────────────
# 主类
# ────────────────────────────────────────────────

class CourseIngestor:
    """
    课件 ingest 入口类。负责文件级别的幂等性管理和双写协调。

    参数：
    - db_dir: ChromaDB 持久化目录（通常 data/chroma）
    - sqlite_path: SQLite 数据库文件路径（通常 data/nexus.db）
    - gemini_api_key: 不传则从环境变量读取
    """

    def __init__(
        self,
        db_dir: Path,
        sqlite_path: Path,
        gemini_api_key: str | None = None,
    ) -> None:
        self.gemini_api_key = gemini_api_key

        # ChromaDB 以 per-course 模式初始化
        self._chroma = ChromaKnowledgeStore(
            persist_dir=db_dir,
            per_course=True,
        )
        # SQLite 存储初始化（自动建表）
        self._sqlite = SQLiteStore(sqlite_path)

    # ────────────────────────────────────────────────
    # 单文件 ingest
    # ────────────────────────────────────────────────

    def ingest_file(
        self,
        filepath: Path,
        course_id: str,
        *,
        force: bool = False,
    ) -> IngestResult:
        """
        将单个文件 ingest 到知识库。

        幂等性流程：
        1. 计算文件 MD5
        2. 查询 SQLite documents 表
           - hash 匹配 + status=done → 跳过（文件未变更）
           - hash 不匹配 → 删除旧数据，重新解析
           - 不存在 → 全新解析
        3. 解析 → 双写 ChromaDB + SQLite

        参数：
        - force=True：忽略 hash 检查，强制重新解析
        """
        if not filepath.exists():
            return IngestResult(
                filepath=filepath, course_id=course_id,
                status="failed", error="文件不存在"
            )

        doc_id = f"{course_id}/{filepath.name}"
        file_hash = _md5(filepath)

        # ── 幂等性检查 ──
        if not force:
            existing = self._sqlite.get_document(doc_id)
            if existing and existing["file_hash"] == file_hash and existing["parse_status"] == "done":
                logger.debug(f"跳过未变更文件: {filepath.name}")
                return IngestResult(
                    filepath=filepath, course_id=course_id,
                    status="skipped",
                    chunks=existing.get("chunk_count", 0),
                )

        # ── 清理旧数据（文件更新时） ──
        self._cleanup_old_data(doc_id, course_id, filepath.name)

        # ── 注册文档，状态置为 parsing ──
        self._sqlite.upsert_document(
            doc_id=doc_id,
            course_id=course_id,
            filename=filepath.name,
            file_hash=file_hash,
            parse_status="parsing",
        )

        # ── 解析 ──
        try:
            result = parse_document(filepath, gemini_api_key=self.gemini_api_key)
        except Exception as exc:
            self._sqlite.set_document_status(doc_id, "failed")
            logger.error(f"解析失败 {filepath.name}: {exc}")
            return IngestResult(
                filepath=filepath, course_id=course_id,
                status="failed", error=str(exc)
            )

        if not result.chunks:
            self._sqlite.set_document_status(doc_id, "failed")
            return IngestResult(
                filepath=filepath, course_id=course_id,
                status="failed", error="解析结果为空",
                warnings=result.warnings,
            )

        # ── 双写 ChromaDB + SQLite ──
        lecture_number = _infer_lecture_number(filepath.name)
        self._write_chunks(
            chunks=result.chunks,
            doc_id=doc_id,
            course_id=course_id,
            filename=filepath.name,
            lecture_number=lecture_number,
            parse_model="gemini-2.0-flash" if result.parse_method == "gemini_vision" else "text_fallback",
        )

        # ── 更新文档状态为 done ──
        self._sqlite.set_document_status(
            doc_id, "done", chunk_count=len(result.chunks)
        )
        self._sqlite.upsert_document(
            doc_id=doc_id,
            course_id=course_id,
            filename=filepath.name,
            file_hash=file_hash,
            parse_status="done",
            chunk_count=len(result.chunks),
            parse_model="gemini-2.0-flash" if result.parse_method == "gemini_vision" else None,
        )

        logger.info(
            f"完成 {filepath.name}: {len(result.chunks)} chunks "
            f"via {result.parse_method}"
        )

        return IngestResult(
            filepath=filepath,
            course_id=course_id,
            status="ingested",
            chunks=len(result.chunks),
            parse_method=result.parse_method,
            warnings=result.warnings,
        )

    # ────────────────────────────────────────────────
    # 批量 ingest
    # ────────────────────────────────────────────────

    def ingest_directory(
        self,
        directory: Path,
        course_id: str,
        *,
        extensions: tuple[str, ...] = (".pdf", ".pptx", ".docx"),
        force: bool = False,
    ) -> list[IngestResult]:
        """
        批量 ingest 目录下所有支持格式的文件。
        返回每个文件的 IngestResult 列表。
        """
        files = [
            f for f in sorted(directory.iterdir())
            if f.is_file() and f.suffix.lower() in extensions
        ]

        if not files:
            logger.warning(f"目录 {directory} 下没有找到支持的文件")
            return []

        results: list[IngestResult] = []
        for fp in files:
            logger.info(f"处理: {fp.name}")
            r = self.ingest_file(fp, course_id, force=force)
            results.append(r)
            print(r)  # 命令行使用时打印进度

        # 汇总统计
        ok = sum(1 for r in results if r.status == "ingested")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed = sum(1 for r in results if r.status == "failed")
        total_chunks = sum(r.chunks for r in results if r.status == "ingested")
        logger.info(
            f"批量完成: {ok} 成功 / {skipped} 跳过 / {failed} 失败 | "
            f"共 {total_chunks} chunks"
        )

        return results

    # ────────────────────────────────────────────────
    # 内部方法
    # ────────────────────────────────────────────────

    def _cleanup_old_data(self, doc_id: str, course_id: str, filename: str) -> None:
        """
        清理旧版本数据（文件 hash 不一致时触发）。
        - SQLite：DELETE FROM chunks WHERE document_id=? （CASCADE 自动处理）
        - ChromaDB：找出旧 chunk_id 并删除对应向量
        """
        existing = self._sqlite.get_document(doc_id)
        if not existing:
            return

        # 从 SQLite 获取旧 chunk 的 ID 列表（用于 ChromaDB 删除）
        # 此时利用 SQLiteStore 底层连接查询
        import sqlite3
        conn = sqlite3.connect(str(self._sqlite.db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute(
            "SELECT chunk_id FROM chunks WHERE document_id=?", (doc_id,)
        ).fetchall()
        old_chunk_ids = [r[0] for r in rows]
        conn.close()

        # 先删 ChromaDB 向量（SQLite CASCADE 删除在 delete_document 时触发）
        if old_chunk_ids:
            self._chroma.delete_chunks_by_ids(old_chunk_ids, course_id=course_id)
            logger.debug(f"从 ChromaDB 删除 {len(old_chunk_ids)} 个旧 chunks: {filename}")

        # 再删 SQLite 记录（CASCADE 自动清理 chunks 表）
        self._sqlite.delete_document(doc_id)

    def _write_chunks(
        self,
        chunks: list[ParsedChunk],
        doc_id: str,
        course_id: str,
        filename: str,
        lecture_number: int | None,
        parse_model: str,
    ) -> None:
        """
        将 ParsedChunk 列表同时写入 ChromaDB（向量）和 SQLite（元数据）。
        两个存储使用相同的 chunk_id，确保可以交叉查询。
        """
        ids: list[str] = []
        texts: list[str] = []
        chroma_metas: list[dict] = []

        for idx, chunk in enumerate(chunks):
            cid = _chunk_id(course_id, filename, idx)
            ids.append(cid)
            texts.append(chunk.content)           # content 作为 embedding 输入
            chroma_metas.append(
                _chunk_to_metadata(chunk, course_id, filename, lecture_number)
            )

            # 写入 SQLite chunk 元数据
            self._sqlite.upsert_chunk(
                chunk_id=cid,
                document_id=doc_id,
                course_id=course_id,
                topic=chunk.topic,
                chunk_type=chunk.type,
                keywords=chunk.keywords,
                prerequisites=chunk.prerequisites,
                page=chunk.page,
                lecture_number=lecture_number,
            )

        # 批量写入 ChromaDB（embedding 在此发生）
        self._chroma.upsert_chunks(ids, texts, chroma_metas, course_id=course_id)
        logger.debug(f"写入 {len(ids)} chunks 到 ChromaDB + SQLite: {filename}")
