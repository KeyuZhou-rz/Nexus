"""
完整 Ingest Pipeline — Nexus Phase 3

功能：
- 幂等性：用 SHA-256 file_hash 检测文件是否已解析，跳过或增量更新
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

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .concept_extractor import ConceptExtractor
from .ingestion_service import chunk_id, document_id, file_content_hash
from .maintenance_lock import maintenance_lock
from .parser import parse_document, ParsedChunk
from .store import ChromaKnowledgeStore
from .sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────

def _file_hash(filepath: Path) -> str:
    """Compute the canonical SHA-256 content hash."""
    return file_content_hash(filepath)


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


def _chunk_to_metadata(
    chunk: ParsedChunk,
    course_id: str,
    filename: str,
    lecture_number: int | None,
    file_hash: str,
    chunk_index: int,
    chunk_identifier: str,
) -> dict[str, str | int]:
    """
    将 ParsedChunk 转为 ChromaDB metadata 字典。
    ChromaDB 不支持 list 类型，keywords/prerequisites 转为逗号分隔字符串。
    """
    doc_id = document_id(course_id, filename)
    return {
        "schema_version": "1",
        "document_id": doc_id,
        "chunk_id": chunk_identifier,
        "course_id": course_id,
        "source_file": filename,
        "file_name": filename,
        "source_path": filename,
        "file_hash": file_hash,
        "chunk_index": chunk_index,
        "topic": chunk.topic,
        "type": chunk.type or "overview",
        "doc_type": chunk.type or "overview",
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
    parse_method: str = ""                 # "deepseek" | "deepseek_cached" | "text_fallback"
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
    - llm_api_key: 不传则通过 LLMGateway 读取
    """

    def __init__(
        self,
        db_dir: Path,
        sqlite_path: Path,
        llm_api_key: str | None = None,
    ) -> None:
        self.llm_api_key = llm_api_key

        # ChromaDB 以 per-course 模式初始化
        self._chroma = ChromaKnowledgeStore(
            persist_dir=db_dir,
            per_course=True,
        )
        # SQLite 存储初始化（自动建表）
        self._sqlite = SQLiteStore(sqlite_path)
        recovered = self._sqlite.recover_stale_parsing()
        if recovered:
            logger.warning("恢复 %d 个中断的解析任务为 pending", recovered)
        # Phase 4A: 概念提取器
        self._concept_extractor = ConceptExtractor(
            sqlite_store=self._sqlite,
            llm_api_key=self.llm_api_key,
        )

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
        with maintenance_lock(
            sqlite_path=self._sqlite.db_path,
            chroma_path=getattr(self._chroma, "persist_dir", None),
            exclusive=False,
        ):
            return self._ingest_file_unlocked(filepath, course_id, force=force)

    def _ingest_file_unlocked(
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

        doc_id = document_id(course_id, filepath.name)
        file_hash = _file_hash(filepath)

        # ── 幂等性检查 ──
        if not force:
            existing = self._sqlite.get_document(doc_id)
            if existing and existing["file_hash"] == file_hash and existing["parse_status"] == "done":
                try:
                    complete = self._document_is_complete(doc_id, course_id, existing)
                except Exception as exc:
                    return IngestResult(
                        filepath=filepath,
                        course_id=course_id,
                        status="failed",
                        error=f"索引一致性检查失败: {exc}",
                    )
                if complete:
                    logger.debug(f"跳过未变更文件: {filepath.name}")
                    return IngestResult(
                        filepath=filepath, course_id=course_id,
                        status="skipped",
                        chunks=existing.get("chunk_count", 0),
                    )

        # ── 清理旧数据（文件更新时） ──
        try:
            self._cleanup_old_data(doc_id, course_id, filepath.name)
        except Exception as exc:
            return IngestResult(
                filepath=filepath,
                course_id=course_id,
                status="failed",
                error=f"旧索引清理失败: {exc}",
            )

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
            result = parse_document(filepath)
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
        try:
            self._write_chunks(
                chunks=result.chunks,
                doc_id=doc_id,
                course_id=course_id,
                filename=filepath.name,
                lecture_number=lecture_number,
                file_hash=file_hash,
            )

            # ── Phase 4A: 概念提取 ──
            chunk_ids_list = [
                chunk_id(course_id, file_hash, i)
                for i in range(len(result.chunks))
            ]
            self._extract_concepts(result.chunks, chunk_ids_list, course_id)
        except Exception as exc:
            self._sqlite.set_document_status(doc_id, "failed")
            logger.error("入库失败 %s: %s", filepath.name, exc)
            return IngestResult(
                filepath=filepath,
                course_id=course_id,
                status="failed",
                error=str(exc),
                warnings=result.warnings,
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
            parse_model="deepseek-chat" if result.parse_method in ("deepseek", "deepseek_cached") else None,
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

    def _extract_concepts(
        self,
        chunks: list[ParsedChunk],
        chunk_ids: list[str],
        course_id: str,
    ) -> dict[str, int]:
        """
        Phase 4A: 从解析结果中提取概念和关系，写入知识图谱。
        整体 try/except — 概念提取失败不影响 ingest 成功。
        """
        try:
            # Pass 1: 规则提取
            candidates = self._concept_extractor.extract_from_chunks(
                chunks, chunk_ids, course_id
            )
            # Pass 2: LLM 精炼
            refined, relations = self._concept_extractor.refine_with_llm(
                candidates, chunks, chunk_ids, course_id
            )
            # Pass 3: 跨课程关系发现
            cross_relations = self._concept_extractor.discover_cross_course_relations(
                refined, course_id
            )
            relations.extend(cross_relations)
            # 持久化
            stats = self._concept_extractor.persist(refined, relations)
            logger.info(
                f"概念图谱: {stats['concepts']} concepts, "
                f"{stats['relations']} relations ({course_id})"
            )
            return stats
        except Exception as exc:
            logger.warning(f"概念提取失败（非致命）: {exc}")
            return {"concepts": 0, "relations": 0}

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
        # 复用 SQLiteStore 的连接工厂以保证 WAL/foreign_keys 一致
        from .sqlite_store import connect
        with connect(self._sqlite.db_path) as conn:
            rows = conn.execute(
                "SELECT chunk_id FROM chunks WHERE document_id=?", (doc_id,)
            ).fetchall()
        old_chunk_ids = [r[0] for r in rows]

        # 先删 ChromaDB 向量（SQLite CASCADE 删除在 delete_document 时触发）
        if old_chunk_ids:
            self._chroma.delete_chunks_by_ids(old_chunk_ids, course_id=course_id)
            logger.debug(f"从 ChromaDB 删除 {len(old_chunk_ids)} 个旧 chunks: {filename}")

        # 再删 SQLite 记录（CASCADE 自动清理 chunks 表）
        self._sqlite.delete_document(doc_id)

    def _document_is_complete(
        self, doc_id: str, course_id: str, document: dict
    ) -> bool:
        """Verify both stores before treating an indexed document as complete."""
        from .sqlite_store import connect

        with connect(self._sqlite.db_path) as conn:
            rows = conn.execute(
                "SELECT chunk_id FROM chunks WHERE document_id=? ORDER BY chunk_id", (doc_id,)
            ).fetchall()
        ids = [str(row[0]) for row in rows]
        expected_count = int(document.get("chunk_count") or 0)
        return bool(ids) and len(ids) == expected_count and self._chroma.has_chunk_ids(
            ids, course_id=course_id
        )

    def _write_chunks(
        self,
        chunks: list[ParsedChunk],
        doc_id: str,
        course_id: str,
        filename: str,
        lecture_number: int | None,
        file_hash: str,
    ) -> None:
        """
        将 ParsedChunk 列表同时写入 ChromaDB（向量）和 SQLite（元数据）。
        两个存储使用相同的 chunk_id，确保可以交叉查询。
        """
        ids: list[str] = []
        texts: list[str] = []
        chroma_metas: list[dict] = []

        for idx, chunk in enumerate(chunks):
            cid = chunk_id(course_id, file_hash, idx)
            ids.append(cid)
            texts.append(chunk.content)           # content 作为 embedding 输入
            chroma_metas.append(
                _chunk_to_metadata(
                    chunk, course_id, filename, lecture_number,
                    file_hash, idx, cid,
                )
            )

        try:
            for cid, chunk in zip(ids, chunks):
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
            self._chroma.upsert_chunks(ids, texts, chroma_metas, course_id=course_id)
            if not self._chroma.has_chunk_ids(ids, course_id=course_id):
                raise RuntimeError("Chroma write verification failed")
        except Exception:
            chroma_clean = False
            try:
                self._chroma.delete_chunks_by_ids(ids, course_id=course_id)
                chroma_clean = True
            except Exception as cleanup_exc:
                logger.warning("Chroma 补偿清理失败: %s", cleanup_exc)
            # If Chroma cleanup fails, keep SQLite IDs attached to the failed
            # document so the next retry can reconcile the orphan vectors.
            if chroma_clean:
                self._sqlite.delete_chunks_by_document(doc_id)
            raise
        logger.debug(f"写入 {len(ids)} chunks 到 ChromaDB + SQLite: {filename}")
