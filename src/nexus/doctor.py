from __future__ import annotations

import importlib.util
import stat
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, default_config
from .storage import load_feeds


@dataclass
class DoctorReport:
    ok: bool
    failures: list[str]
    warnings: list[str]
    details: dict[str, object]


def _module_available(name: str) -> bool:
    return bool(importlib.util.find_spec(name))


def _check_secret_permissions(path: Path, label: str) -> tuple[str, str] | None:
    """Return a redacted permission warning without reading secret contents."""
    if not path.exists():
        return None
    if path.is_symlink():
        return (label, f"{label} must not be a symlink: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return (label, f"{label} is readable by group/others ({mode:04o}); run chmod 600 {path}")
    return None


def run_doctor(config: AppConfig | None = None, include_google: bool = True) -> DoctorReport:
    config = config or default_config()
    failures: list[str] = []
    warnings: list[str] = []

    secret_paths = {
        ".env": Path(__file__).resolve().parents[2] / ".env",
        "legacy_llm_key": Path(config.data_dir) / "QWEN_API_KEY.json",
    }
    if config.google_credentials_path:
        secret_paths["google_credentials"] = Path(config.google_credentials_path)
    if config.google_token_path:
        secret_paths["google_token"] = Path(config.google_token_path)
    secret_permissions: dict[str, str] = {}
    for label, path in secret_paths.items():
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
            secret_permissions[label] = f"{mode:04o}"
        warning = _check_secret_permissions(path, label)
        if warning:
            warnings.append(warning[1])

    core_deps = {
        "requests": _module_available("requests"),
        "feedparser": _module_available("feedparser"),
        "icalendar": _module_available("icalendar"),
    }
    for name, installed in core_deps.items():
        if not installed:
            failures.append(f"Missing dependency: {name}")

    # Storage checks are deliberately read-only. A fresh checkout is allowed
    # to have no database/vector index yet; corruption, however, is actionable.
    data_dir = Path(config.data_dir).expanduser()
    if not data_dir.exists():
        warnings.append(f"Data directory does not exist yet: {data_dir} (run ingest/pipeline to initialize it).")
    elif not data_dir.is_dir():
        failures.append(f"Data directory is not a directory: {data_dir}")

    sqlite_path = data_dir / "nexus.db"
    sqlite_status = "missing"
    sqlite_integrity: str | None = None
    if sqlite_path.exists():
        try:
            with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2) as conn:
                sqlite_integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            sqlite_status = "ok" if sqlite_integrity == "ok" else "corrupt"
            if sqlite_status == "corrupt":
                failures.append(f"SQLite integrity check failed for {sqlite_path}: {sqlite_integrity}")
        except (sqlite3.Error, OSError) as exc:
            sqlite_status = "unreadable"
            failures.append(f"SQLite database is unreadable ({sqlite_path}): {exc}")
    else:
        warnings.append(f"SQLite database not initialized: {sqlite_path}")

    chroma_path = data_dir / "chroma"
    chroma_status = "present" if chroma_path.exists() else "missing"
    if not chroma_path.exists():
        warnings.append(f"Chroma index not initialized: {chroma_path}")
    elif not chroma_path.is_dir():
        chroma_status = "invalid"
        failures.append(f"Chroma path is not a directory: {chroma_path}")

    google_deps = {
        "googleapiclient": _module_available("googleapiclient"),
        "google_auth_oauthlib": _module_available("google_auth_oauthlib"),
        "google_auth_httplib2": _module_available("google_auth_httplib2"),
    }
    if include_google:
        for name, installed in google_deps.items():
            if not installed:
                warnings.append(f"Google dependency missing: {name}")

        cred_ok = bool(config.google_credentials_path and Path(config.google_credentials_path).exists())
        token_ok = bool(config.google_token_path and Path(config.google_token_path).exists())
        if not cred_ok:
            warnings.append("Google client secret file missing.")
        if not token_ok:
            warnings.append("Google token file missing.")

    try:
        feeds = load_feeds(config.feeds_path) if config.feeds_path else []
    except (OSError, ValueError, TypeError) as exc:
        feeds = []
        failures.append(f"Unable to read feeds configuration: {exc}")
    enabled_feeds = [f for f in feeds if f.enabled]
    if not enabled_feeds:
        warnings.append("No enabled Brightspace feeds found (data/feeds.json).")

    tasks_path = config.data_dir / "tasks.json"
    details: dict[str, object] = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "data_dir": str(config.data_dir),
        "data_dir_exists": data_dir.exists(),
        "sqlite_path": str(sqlite_path),
        "sqlite_status": sqlite_status,
        "sqlite_integrity": sqlite_integrity,
        "chroma_path": str(chroma_path),
        "chroma_status": chroma_status,
        "tasks_path": str(tasks_path),
        "tasks_exists": tasks_path.exists(),
        "tasks_size": tasks_path.stat().st_size if tasks_path.exists() else 0,
        "feeds_path": str(config.feeds_path) if config.feeds_path else None,
        "feeds_enabled": len(enabled_feeds),
        "deps": {**core_deps, **google_deps},
        "llm": {
            "enabled": bool(config.llm_enabled),
            "configured": bool(config.llm_api_key),
            "base_url": bool(config.llm_base_url),
            "model": config.llm_model or "",
        },
        "secret_permissions": secret_permissions,
    }

    return DoctorReport(
        ok=not failures,
        failures=failures,
        warnings=warnings,
        details=details,
    )
