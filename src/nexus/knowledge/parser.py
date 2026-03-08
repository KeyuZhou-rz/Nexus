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
# LLM 解析（QWEN-Long via DashScope Files API）
# ────────────────────────────────────────────────

DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_MIME_MAP = {
    ".pdf":  "application/pdf",
    ".ppt":  "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _upload_file_to_qwen(filepath: Path, api_key: str) -> str:
    """将文件上传到 DashScope Files API，返回 file_id。"""
    import requests

    mime_type = _MIME_MAP.get(filepath.suffix.lower(), "application/octet-stream")
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{DASHSCOPE_BASE}/files",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filepath.name, f, mime_type)},
            data={"purpose": "file-extract"},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.json()["id"]


def _call_qwen_with_file(file_id: str, api_key: str) -> list[dict[str, Any]]:
    """用已上传的 file_id 调用 qwen-long 解析，返回 chunks 列表。"""
    import requests

    resp = requests.post(
        f"{DASHSCOPE_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "qwen-long",
            "messages": [
                {"role": "system", "content": f"fileid://{file_id}"},
                {"role": "user",   "content": PARSE_PROMPT},
            ],
            "temperature": 0.1,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        },
        timeout=180,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # 去掉可能的 markdown 包装
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)
    # prompt 要求 {"chunks": [...]}，兼容模型直接返回列表的情况
    if isinstance(result, list):
        return result
    return result.get("chunks") or result.get("items") or []


def _parse_with_qwen(filepath: Path, api_key: str) -> list[dict[str, Any]]:
    """
    使用 qwen-long 解析完整 PDF/文档。
    流程：上传文件 → fileid 注入 → LLM 语义切块。
    """
    file_id = _upload_file_to_qwen(filepath, api_key)
    logger.info(f"文件已上传: {file_id}")
    return _call_qwen_with_file(file_id, api_key)


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
    llm_api_key: str | None = None,
    large_doc_threshold: int = 50,
) -> ParseResult:
    """
    解析文档，返回 ParseResult（包含 chunks、解析方法、警告）。

    参数：
    - llm_api_key: 不传则从环境变量 QWEN_API_KEY 读取
    - large_doc_threshold: 保留参数（qwen-long 支持整文档，无需分段）
    """
    api_key = llm_api_key or os.getenv("QWEN_API_KEY")

    # 尝试读取页数（用于质量检查）
    page_count = 0
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(filepath))
        page_count = len(reader.pages)
    except Exception:
        pass

    # ── 路径 1: QWEN-Long（DashScope Files API）──
    if api_key:
        try:
            logger.info(f"使用 QWEN-Long 解析 {filepath.name}（{page_count} 页）")
            raw_chunks = _parse_with_qwen(filepath, api_key)

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

            chunks = _merge_continues(chunks)
            warnings = _check_quality(chunks, page_count)
            for w in warnings:
                logger.warning(f"[质量检查] {filepath.name}: {w}")

            return ParseResult(
                chunks=chunks,
                parse_method="qwen_long",
                page_count=page_count,
                warnings=warnings,
            )

        except Exception as exc:
            logger.warning(f"QWEN 解析失败，回退到文本提取: {exc}")

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
