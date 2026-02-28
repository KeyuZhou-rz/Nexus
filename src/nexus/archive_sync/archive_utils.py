from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".zip",
    ".txt",
    ".csv",
}


def is_supported_attachment(url: str, filename: str | None = None) -> bool:
    candidate = (filename or url).lower().split("?")[0]
    return any(candidate.endswith(ext) for ext in _ALLOWED_EXTENSIONS)


def sanitize_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


def extension_from_url(url: str, fallback: str = ".bin") -> str:
    raw = url.lower().split("?")[0]
    ext = Path(raw).suffix
    return ext if ext else fallback


def build_archive_filename(course: str, title: str, due_at: datetime | None, source_url: str) -> str:
    due_text = due_at.strftime("%Y%m%d") if due_at else "nodue"
    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:8]
    ext = extension_from_url(source_url)
    course_part = sanitize_segment(course)
    title_part = sanitize_segment(title)
    return f"{course_part}_{title_part}_{due_text}_{digest}{ext}"
