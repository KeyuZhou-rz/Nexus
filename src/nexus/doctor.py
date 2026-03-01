from __future__ import annotations

import importlib.util
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


def run_doctor(config: AppConfig | None = None, include_google: bool = True) -> DoctorReport:
    config = config or default_config()
    failures: list[str] = []
    warnings: list[str] = []

    core_deps = {
        "requests": _module_available("requests"),
        "feedparser": _module_available("feedparser"),
        "icalendar": _module_available("icalendar"),
    }
    for name, installed in core_deps.items():
        if not installed:
            failures.append(f"Missing dependency: {name}")

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

    feeds = load_feeds(config.feeds_path) if config.feeds_path else []
    enabled_feeds = [f for f in feeds if f.enabled]
    if not enabled_feeds:
        warnings.append("No enabled Brightspace feeds found (data/feeds.json).")

    tasks_path = config.data_dir / "tasks.json"
    details: dict[str, object] = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "data_dir": str(config.data_dir),
        "tasks_path": str(tasks_path),
        "tasks_exists": tasks_path.exists(),
        "tasks_size": tasks_path.stat().st_size if tasks_path.exists() else 0,
        "feeds_path": str(config.feeds_path) if config.feeds_path else None,
        "feeds_enabled": len(enabled_feeds),
        "deps": {**core_deps, **google_deps},
    }

    return DoctorReport(
        ok=not failures,
        failures=failures,
        warnings=warnings,
        details=details,
    )
