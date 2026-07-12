from __future__ import annotations

import html
import importlib
import re
import zipfile
from pathlib import Path
from typing import Any

SUPPORTED_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".docx", ".pdf"}


def _normalize_text_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value if item)
    if isinstance(value, dict):
        for key in ("markdown", "text", "content", "output"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return ""


def _extract_text_from_pdf_pymupdf4llm(path: Path) -> str:
    """Try pymupdf4llm first for markdown-rich PDF extraction."""
    try:
        mod = importlib.import_module("pymupdf4llm")
    except Exception:
        return ""

    candidate_names = (
        "to_markdown",
        "convert_pdf_to_markdown",
        "pdf_to_markdown",
        "extract_markdown",
    )
    for name in candidate_names:
        func = getattr(mod, name, None)
        if not callable(func):
            continue
        for arg in (str(path), path):
            try:
                text = _normalize_text_result(func(arg))
            except TypeError:
                continue
            except Exception:
                text = ""
            if text.strip():
                return text
    return ""


def _extract_text_from_pdf_pypdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(str(path))
        texts = [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        return ""
    return "\n".join(part.strip() for part in texts if part.strip())


def _extract_text_from_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    except Exception:
        return ""
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.DOTALL)
    return html.unescape("\n".join(part.strip() for part in parts if part.strip()))


def extract_text(path: Path) -> str:
    """Extract plain/markdown-ish text from supported files."""
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".docx":
        return _extract_text_from_docx(path)
    if suffix == ".pdf":
        # Prefer markdown-friendly extraction if pymupdf4llm is available.
        text = _extract_text_from_pdf_pymupdf4llm(path)
        if text.strip():
            return text
        return _extract_text_from_pdf_pypdf(path)
    return ""
