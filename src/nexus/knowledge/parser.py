"""
LLM 课件解析器 — Nexus Phase 3

解析策略（优先级从高到低）：
1. Gemini Flash Vision：整文档上传，LLM 直接理解图表/公式/排版，输出语义 chunks
2. 分段解析：文档超过 50 页时，每批 25 页分段解析后合并
3. 文本提取 fallback：Gemini 不可用时，用 pymupdf4llm/pypdf 提取纯文本后按段落切分

Gemini 配置：
- temperature=0.1（解析任务需要确定性，减少幻觉）
- response_mime_type="application/json"（强制 JSON 输出）
- 模型：gemini-2.0-flash（成本低，Vision 能力足够）

跨页合并：
- LLM 输出的 chunk 若 continues=true，与后续 chunk 合并为一个知识点
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parseprompt import PARSE_PROMPT
from .document_text import extract_text

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
    parse_method: str               # "gemini_vision" | "text_fallback"
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
# LLM 解析（Gemini Flash Vision）
# ────────────────────────────────────────────────

def _parse_with_gemini(filepath: Path, api_key: str) -> list[dict[str, Any]]:
    """
    使用 Gemini Flash Vision 解析 PDF/文档。
    将文件作为 blob 上传，让模型直接理解图表、公式、排版。
    返回原始 JSON 解析结果列表（未合并 continues）。
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    # 读取文件字节，上传给 Gemini
    file_bytes = filepath.read_bytes()
    suffix = filepath.suffix.lower()

    # 确定 MIME 类型
    mime_map = {
        ".pdf": "application/pdf",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    mime_type = mime_map.get(suffix, "application/octet-stream")

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            PARSE_PROMPT,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            max_output_tokens=8192,
        ),
    )

    # 解析 JSON 响应
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    return json.loads(raw_text)


def _parse_with_gemini_batched(
    filepath: Path,
    api_key: str,
    batch_size: int = 25,
) -> list[dict[str, Any]]:
    """
    分段解析策略：文档超过 50 页时使用。
    用 pypdf 将 PDF 按 batch_size 页拆分后逐批送给 Gemini，结果合并。
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        logger.warning("pypdf 未安装，无法分段解析，回退全文解析")
        return _parse_with_gemini(filepath, api_key)

    reader = PdfReader(str(filepath))
    total_pages = len(reader.pages)
    all_chunks: list[dict[str, Any]] = []

    for start in range(0, total_pages, batch_size):
        end = min(start + batch_size, total_pages)
        logger.info(f"分段解析第 {start+1}-{end} 页（共 {total_pages} 页）")

        # 将这批页写入临时 PDF 字节流
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])

        buf = io.BytesIO()
        writer.write(buf)
        batch_bytes = buf.getvalue()

        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(data=batch_bytes, mime_type="application/pdf"),
                PARSE_PROMPT,
            ],
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                max_output_tokens=8192,
            ),
        )
        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        batch_result = json.loads(raw_text)
        all_chunks.extend(batch_result)

    return all_chunks


# ────────────────────────────────────────────────
# 文本 fallback
# ────────────────────────────────────────────────

def _parse_text_fallback(filepath: Path) -> list[ParsedChunk]:
    """
    当 Gemini 不可用时的 fallback 方案。
    使用 document_text.extract_text 提取纯文本，按段落切分为 chunks。
    质量比 Gemini 差（无法理解图表/公式），但总比报错要好。
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
    gemini_api_key: str | None = None,
    large_doc_threshold: int = 50,  # 超过此页数改用分段解析
) -> ParseResult:
    """
    解析文档，返回 ParseResult（包含 chunks、解析方法、警告）。

    参数：
    - gemini_api_key: 不传则从环境变量 GEMINI_API_KEY 或 GOOGLE_API_KEY 读取
    - large_doc_threshold: 超过此页数的 PDF 使用分段解析策略
    """
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    # 尝试读取页数（用于质量检查和分段策略决策）
    page_count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(filepath))
        page_count = len(reader.pages)
    except Exception:
        pass

    # ── 路径 1: Gemini Flash Vision ──
    if api_key:
        try:
            logger.info(f"使用 Gemini Vision 解析 {filepath.name}（{page_count} 页）")

            # 超过阈值时分段解析，避免单次请求过大
            if page_count > large_doc_threshold:
                raw_chunks = _parse_with_gemini_batched(filepath, api_key)
            else:
                raw_chunks = _parse_with_gemini(filepath, api_key)

            # 将原始字典列表转为 ParsedChunk 对象
            chunks: list[ParsedChunk] = []
            for item in raw_chunks:
                if not isinstance(item, dict):
                    continue
                chunks.append(ParsedChunk(
                    topic=item.get("topic", ""),
                    content=item.get("content", ""),
                    type=item.get("type", "overview"),
                    keywords=item.get("keywords") or [],
                    prerequisites=item.get("prerequisites") or [],
                    page=item.get("page"),
                    continues=bool(item.get("continues", False)),
                ))

            # 跨页合并
            chunks = _merge_continues(chunks)

            # 质量检查
            warnings = _check_quality(chunks, page_count)
            if warnings:
                for w in warnings:
                    logger.warning(f"[质量检查] {filepath.name}: {w}")

            return ParseResult(
                chunks=chunks,
                parse_method="gemini_vision",
                page_count=page_count,
                warnings=warnings,
            )

        except Exception as exc:
            logger.warning(
                f"Gemini 解析失败，回退到文本提取: {exc}"
            )

    # ── 路径 2: 文本提取 fallback ──
    logger.info(f"使用文本提取 fallback 解析 {filepath.name}")
    fallback_chunks = _parse_text_fallback(filepath)
    warnings = _check_quality(fallback_chunks, page_count)

    return ParseResult(
        chunks=fallback_chunks,
        parse_method="text_fallback",
        page_count=page_count,
        warnings=warnings,
    )
