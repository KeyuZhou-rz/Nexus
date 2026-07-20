"""
LLM 课件解析器 — Nexus Phase N2

解析策略（优先级从高到低）：
1. DeepSeek 语义解析：提取文本 → SHA-256 哈希 → 查缓存 → gateway.chat() 解析
2. 文本提取 fallback：LLM 不可用时，用 pymupdf4llm/pypdf 提取纯文本后按段落切分

缓存：
- 缓存键 = SHA-256(文件内容) + course_id
- 相同文件自动命中，文件修改自动失效
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parseprompt import PARSE_PROMPT
from .document_text import extract_text
from .llm_gateway import gateway, LLMGatewayError

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────
# 数据结构
# ────────────────────────────────────────────────

@dataclass
class ParsedChunk:
    """一个知识点 chunk，来自 LLM 解析结果。"""
    topic: str
    content: str
    type: str = "overview"           # definition|algorithm|example|theorem|overview|code|exercise
    keywords: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    page: int | None = None
    continues: bool = False          # True 表示该 chunk 跨页，需要后处理合并


@dataclass
class ParseResult:
    """文档解析结果。"""
    chunks: list[ParsedChunk]
    parse_method: str               # "deepseek" | "deepseek_cached" | "text_fallback"
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


# ────────────────────────────────────────────────
# 质量检查
# ────────────────────────────────────────────────

def _check_quality(chunks: list[ParsedChunk], page_count: int) -> list[str]:
    """
    质量检查，返回警告信息列表（不是 exception，只是 warning）。
    检查项：
    - chunk 数量 > 0
    - 每个 chunk content 长度 >= 50 字符
    - 每个 chunk 必须有 topic 和至少 1 个 keyword
    - 总文本量与页数比值合理（每页至少 100 字）
    """
    warnings: list[str] = []

    if not chunks:
        warnings.append("解析结果为空，无任何 chunk 输出")
        return warnings

    total_chars = 0
    for i, chunk in enumerate(chunks):
        if not chunk.topic:
            warnings.append(f"Chunk {i}: topic 为空")
        if len(chunk.content) < 50:
            warnings.append(f"Chunk {i} '{chunk.topic}': content 过短 ({len(chunk.content)} 字符)")
        if not chunk.keywords:
            warnings.append(f"Chunk {i} '{chunk.topic}': keywords 为空")
        total_chars += len(chunk.content)

    if page_count > 0 and total_chars / page_count < 100:
        warnings.append(
            f"内容密度过低：{total_chars} 字符 / {page_count} 页 = {total_chars/page_count:.0f} 字符/页"
        )

    return warnings


# ────────────────────────────────────────────────
# 跨页合并
# ────────────────────────────────────────────────

def _merge_continues(chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    """
    合并 continues=True 的连续 chunks。
    规则：
    - content 用 \\n\\n 拼接
    - keywords / prerequisites 取并集去重
    - topic / page / type 使用首个 chunk 的值
    - 合并后 continues 置为 False
    """
    if not chunks:
        return []

    merged: list[ParsedChunk] = []
    buf: ParsedChunk | None = None

    for chunk in chunks:
        if buf is None:
            # 新的起点
            buf = ParsedChunk(
                topic=chunk.topic,
                content=chunk.content,
                type=chunk.type,
                keywords=list(chunk.keywords),
                prerequisites=list(chunk.prerequisites),
                page=chunk.page,
                continues=chunk.continues,
            )
        else:
            # 接续前一个 chunk：拼接内容，合并 keywords/prerequisites
            buf.content = buf.content.rstrip() + "\n\n" + chunk.content.lstrip()
            buf.keywords = list(dict.fromkeys(buf.keywords + chunk.keywords))
            buf.prerequisites = list(dict.fromkeys(buf.prerequisites + chunk.prerequisites))
            buf.continues = chunk.continues  # 继续传递 continues 标记

        if not buf.continues:
            # 当前 chunk 不再跨页，输出并重置缓冲
            merged.append(buf)
            buf = None

    # 最后一个 chunk 若还在跨页状态，也输出（LLM 可能漏标）
    if buf is not None:
        buf.continues = False
        merged.append(buf)

    return merged


# ────────────────────────────────────────────────
# LLM 解析（DeepSeek via gateway + 文件哈希缓存）
# ────────────────────────────────────────────────

_MAX_CHARS_PER_BATCH = 8000  # 单次 LLM 调用的文本上限
PARSER_VERSION = "2"
FALLBACK_MODEL_VERSION = "text-fallback-v1"
PARSE_PROMPT_HASH = hashlib.sha256(PARSE_PROMPT.encode("utf-8")).hexdigest()


def _compute_file_hash(filepath: Path) -> str:
    """计算文件内容的 SHA-256 哈希。"""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            sha.update(chunk)
    return sha.hexdigest()


def _cache_model_version() -> str:
    return gateway.model if gateway.is_configured else FALLBACK_MODEL_VERSION


def _chunks_to_dicts(chunks: list[ParsedChunk]) -> list[dict[str, Any]]:
    return [
        {
            "topic": chunk.topic,
            "content": chunk.content,
            "type": chunk.type,
            "keywords": chunk.keywords,
            "prerequisites": chunk.prerequisites,
            "page": chunk.page,
            "continues": chunk.continues,
        }
        for chunk in chunks
    ]


def _chunks_from_dicts(items: list[dict[str, Any]]) -> list[ParsedChunk]:
    return [
        ParsedChunk(
            topic=item.get("topic", ""),
            content=item.get("content", ""),
            type=item.get("type", "overview"),
            keywords=item.get("keywords") or [],
            prerequisites=item.get("prerequisites") or [],
            page=item.get("page"),
            continues=bool(item.get("continues", False)),
        )
        for item in items
        if isinstance(item, dict)
    ]


def _parse_text_with_llm(text: str) -> list[dict[str, Any]]:
    """用 gateway.chat() 对一段文本做语义切块，返回原始 dict 列表。"""
    result = gateway.structured_output(
        messages=[
            {"role": "system", "content": PARSE_PROMPT},
            {"role": "user", "content": text},
        ],
        call_site="document_parse",
        temperature=0.1,
        max_tokens=8192,
    )
    if isinstance(result, list):
        return result
    return result.get("chunks") or result.get("items") or []


def _parse_with_deepseek(text: str) -> list[dict[str, Any]]:
    """
    用 DeepSeek 解析课件文本。
    超大文本自动分片，每片 ≤ _MAX_CHARS_PER_BATCH，结果合并。
    """
    if len(text) <= _MAX_CHARS_PER_BATCH:
        return _parse_text_with_llm(text)

    # 按段落边界分片，尽量保持语义完整
    paragraphs = text.split("\n\n")
    batches: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > _MAX_CHARS_PER_BATCH and current:
            batches.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para) + 2
    if current:
        batches.append("\n\n".join(current))

    logger.info(f"文本分 {len(batches)} 片解析")
    all_chunks: list[dict[str, Any]] = []
    for i, batch in enumerate(batches):
        batch_chunks = _parse_text_with_llm(batch)
        all_chunks.extend(batch_chunks)
        logger.info(f"  第 {i+1}/{len(batches)} 片: {len(batch_chunks)} chunks")
    return all_chunks


# ────────────────────────────────────────────────
# 文本 fallback
# ────────────────────────────────────────────────

def _parse_text_fallback(filepath: Path) -> list[ParsedChunk]:
    """
    当 LLM 不可用时的 fallback 方案。
    使用 document_text.extract_text 提取纯文本，按段落切分为 chunks。
    """
    text = extract_text(filepath)
    if not text.strip():
        return []

    # 按双换行分段，每段作为一个 chunk
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) >= 50]

    chunks: list[ParsedChunk] = []
    for i, para in enumerate(paragraphs):
        # 用段落首行作为 topic（截断到 80 字）
        first_line = para.split("\n")[0][:80]
        chunks.append(ParsedChunk(
            topic=first_line or f"Paragraph {i+1}",
            content=para,
            type="overview",
            keywords=[],
            prerequisites=[],
            page=None,
            continues=False,
        ))

    return chunks


# ────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────

def parse_document(
    filepath: Path,
    *,
    course_id: str = "",
    use_cache: bool = True,
) -> ParseResult:
    """
    解析文档，返回 ParseResult（包含 chunks、解析方法、警告）。

    缓存键：SHA-256(file) + course_id + parser/model/prompt version。
    参数：
    - course_id: 课程标识，用于缓存隔离
    - use_cache: 是否查写缓存（默认 True）
    """
    file_hash = _compute_file_hash(filepath)
    file_size = filepath.stat().st_size

    # 尝试读取页数（用于质量检查）
    page_count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(filepath))
        page_count = len(reader.pages)
    except Exception:
        pass

    model_version = _cache_model_version()

    # ── 路径 0: 缓存命中（LLM 与 fallback 使用相同机制）──
    if use_cache:
        try:
            from .sqlite_store import SQLiteStore, default_db_path
            store = SQLiteStore(default_db_path())
            cached = store.get_parse_cache(
                file_hash,
                course_id,
                parser_version=PARSER_VERSION,
                model_version=model_version,
                prompt_hash=PARSE_PROMPT_HASH,
            )
            if cached:
                logger.info(f"缓存命中: {filepath.name} ({file_hash[:12]}...)")
                chunks = _chunks_from_dicts(cached)
                chunks = _merge_continues(chunks)
                warnings = _check_quality(chunks, page_count)
                return ParseResult(
                    chunks=chunks,
                    parse_method=(
                        "deepseek_cached"
                        if model_version != FALLBACK_MODEL_VERSION
                        else "text_fallback_cached"
                    ),
                    page_count=page_count,
                    warnings=warnings,
                )
        except Exception as exc:
            logger.debug(f"缓存查询跳过: {exc}")

    # ── 路径 1: DeepSeek 语义解析 ──
    if gateway.is_configured:
        try:
            logger.info(f"使用 DeepSeek 解析 {filepath.name}（{page_count} 页）")
            text = extract_text(filepath)
            if not text.strip():
                raise ValueError("文档无可提取文本")

            raw_chunks = _parse_with_deepseek(text)

            chunks = _chunks_from_dicts(raw_chunks)

            chunks = _merge_continues(chunks)
            warnings = _check_quality(chunks, page_count)
            for w in warnings:
                logger.warning(f"[质量检查] {filepath.name}: {w}")

            # 写入缓存
            if use_cache and raw_chunks:
                try:
                    from .sqlite_store import SQLiteStore, default_db_path
                    store = SQLiteStore(default_db_path())
                    store.set_parse_cache(
                        file_hash,
                        course_id,
                        raw_chunks,
                        file_size,
                        parser_version=PARSER_VERSION,
                        model_version=model_version,
                        prompt_hash=PARSE_PROMPT_HASH,
                        parse_method="deepseek",
                    )
                    logger.info(f"缓存已写入: {filepath.name} ({file_hash[:12]}...)")
                except Exception as exc:
                    logger.debug(f"缓存写入跳过: {exc}")

            return ParseResult(
                chunks=chunks,
                parse_method="deepseek",
                page_count=page_count,
                warnings=warnings,
            )

        except (LLMGatewayError, Exception) as exc:
            logger.warning(f"DeepSeek 解析失败，回退到文本提取: {exc}")

    # ── 路径 2: 文本提取 fallback ──
    logger.info(f"使用文本提取 fallback 解析 {filepath.name}")
    fallback_chunks = _parse_text_fallback(filepath)
    warnings = _check_quality(fallback_chunks, page_count)

    if use_cache and fallback_chunks:
        try:
            from .sqlite_store import SQLiteStore, default_db_path
            store = SQLiteStore(default_db_path())
            store.set_parse_cache(
                file_hash,
                course_id,
                _chunks_to_dicts(fallback_chunks),
                file_size,
                parser_version=PARSER_VERSION,
                model_version=FALLBACK_MODEL_VERSION,
                prompt_hash=PARSE_PROMPT_HASH,
                parse_method="text_fallback",
            )
        except Exception as exc:
            logger.debug(f"fallback 缓存写入跳过: {exc}")

    return ParseResult(
        chunks=fallback_chunks,
        parse_method="text_fallback",
        page_count=page_count,
        warnings=warnings,
    )
