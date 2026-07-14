from __future__ import annotations

import importlib.util
import stat
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig, default_config
from .storage import load_feeds


@dataclass
class ComponentStatus:
    """Three-level diagnostics for a single feature.

    enabled    — user has opted in (env var / config toggle)
    configured — credentials/paths exist and have correct permissions
    available  — runtime dependency is importable and functional
    """

    name: str
    enabled: bool = False
    configured: bool = False
    available: bool = False
    details: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DoctorReport:
    ok: bool
    failures: list[str]
    warnings: list[str]
    details: dict[str, object]
    components: dict[str, ComponentStatus] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _module_available(name: str) -> bool:
    return bool(importlib.util.find_spec(name))


def _check_secret_permissions(path: Path, label: str) -> str | None:
    """Return a redacted permission warning if the file is insecure."""
    if not path.exists():
        return None
    if path.is_symlink():
        return f"{label} must not be a symlink: {path}"
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return f"{label} is readable by group/others ({mode:04o}); run chmod 600 {path}"
    return None


# ---------------------------------------------------------------------------
# per-component checkers
# ---------------------------------------------------------------------------

def _check_core(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(name="core", enabled=True, configured=True)

    core_deps = {
        "requests": _module_available("requests"),
        "feedparser": _module_available("feedparser"),
        "icalendar": _module_available("icalendar"),
    }
    comp.details["deps"] = core_deps
    for name, installed in core_deps.items():
        if not installed:
            comp.available = False
            comp.errors.append(f"Missing core dependency: {name}")
        else:
            comp.available = True
    if not comp.errors:
        comp.available = True

    # Python version guard
    py_ver = sys.version_info[:2]
    comp.details["python_version"] = f"{py_ver[0]}.{py_ver[1]}"
    if py_ver < (3, 10):
        comp.available = False
        comp.errors.append(f"Python >=3.10 required, found {py_ver[0]}.{py_ver[1]}")
    elif py_ver > (3, 13):
        comp.warnings.append(f"Python {py_ver[0]}.{py_ver[1]} untested; expected 3.10-3.13")

    return comp


def _check_sqlite(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(name="sqlite", enabled=True)
    data_dir = Path(config.data_dir).expanduser()
    sqlite_path = data_dir / "nexus.db"

    comp.details["path"] = str(sqlite_path)

    if not sqlite_path.exists():
        comp.configured = False
        comp.warnings.append(f"SQLite database not initialized: {sqlite_path}")
        comp.details["status"] = "missing"
        return comp

    comp.configured = True
    try:
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True, timeout=2) as conn:
            # integrity
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            comp.details["integrity"] = integrity
            if integrity != "ok":
                comp.available = False
                comp.errors.append(f"SQLite integrity check failed: {integrity}")
            else:
                comp.available = True

            # WAL mode
            journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
            comp.details["journal_mode"] = journal
            if journal.lower() != "wal":
                comp.warnings.append(f"Journal mode is {journal}, WAL recommended")

            # foreign keys (read-only connections can't set PRAGMA; expected=0)
            fk = str(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            comp.details["foreign_keys"] = fk
            if fk != "1":
                comp.warnings.append(
                    "Foreign keys not enforced on read-only connection (expected; sqlite_store enables WAL+FK on write connections)"
                )

            # table count
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            comp.details["table_count"] = tables

            # document/chunk counts for audit
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            comp.details["documents"] = docs
            comp.details["chunks"] = chunks

            comp.details["status"] = "ok" if comp.available else "corrupt"
    except (sqlite3.Error, OSError) as exc:
        comp.available = False
        comp.errors.append(f"SQLite unreadable: {exc}")
        comp.details["status"] = "unreadable"

    return comp


def _check_chroma(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(name="chroma", enabled=True)
    data_dir = Path(config.data_dir).expanduser()
    chroma_path = data_dir / "chroma"

    comp.details["path"] = str(chroma_path)
    if not chroma_path.exists():
        comp.configured = False
        comp.warnings.append(f"Chroma index not initialized: {chroma_path}")
        comp.details["status"] = "missing"
        return comp

    if not chroma_path.is_dir():
        comp.available = False
        comp.errors.append(f"Chroma path is not a directory: {chroma_path}")
        comp.details["status"] = "invalid"
        return comp

    comp.configured = True
    comp.details["status"] = "present"

    # Check embedding model availability
    if _module_available("chromadb"):
        try:
            import chromadb

            comp.details["chromadb_version"] = chromadb.__version__
            # Warn if below recommended version (0.6.x has known data compat issues)
            version_parts = chromadb.__version__.split(".")
            major = int(version_parts[0]) if version_parts else 0
            if major < 1:
                comp.warnings.append(
                    f"ChromaDB {chromadb.__version__} is below recommended 1.0; "
                    "upgrade with: pip install 'chromadb>=1.0'"
                )

            from chromadb.utils import embedding_functions

            ef = embedding_functions.DefaultEmbeddingFunction()
            comp.details["embedding_model"] = getattr(ef, "_model_name", "all-MiniLM-L6-v2")
            comp.details["chromadb_import"] = True
            comp.available = True
        except Exception as exc:
            comp.warnings.append(f"ChromaDB embedding function failed: {exc}")
            comp.details["chromadb_import"] = True
            comp.details["embedding_error"] = str(exc)
            comp.available = False
    else:
        comp.available = False
        comp.errors.append("chromadb package not installed")
        comp.details["chromadb_import"] = False

    # List collections (may fail on version mismatch — 4B will fix)
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_path))
        collections = client.list_collections()
        comp.details["collections"] = len(collections)
        comp.details["collection_names"] = [c.name for c in collections]
    except Exception as exc:
        comp.warnings.append(
            f"Unable to list ChromaDB collections (possible version mismatch): {exc}. "
            "Run 'nexus db-check' for detailed cross-store audit."
        )
        comp.details["collections"] = -1
        comp.details["collection_error"] = str(exc)

    return comp


def _check_google(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(
        name="google",
        enabled=bool(config.google_credentials_path or config.google_token_path),
    )

    google_deps = {
        "googleapiclient": _module_available("googleapiclient"),
        "google_auth_oauthlib": _module_available("google_auth_oauthlib"),
        "google_auth_httplib2": _module_available("google_auth_httplib2"),
    }
    comp.details["deps"] = google_deps
    for name, installed in google_deps.items():
        if not installed:
            comp.warnings.append(f"Google dependency missing: {name} (install with: pip install nexus-dashboard[google])")

    all_deps_ok = all(google_deps.values())
    comp.available = all_deps_ok

    if not all_deps_ok:
        comp.details["status"] = "deps_missing"
        return comp

    cred_ok = bool(config.google_credentials_path and Path(config.google_credentials_path).exists())
    token_ok = bool(config.google_token_path and Path(config.google_token_path).exists())
    comp.configured = cred_ok or token_ok

    comp.details["credentials_path"] = str(config.google_credentials_path) if config.google_credentials_path else None
    comp.details["token_path"] = str(config.google_token_path) if config.google_token_path else None
    comp.details["credentials_exists"] = cred_ok
    comp.details["token_exists"] = token_ok

    if not cred_ok:
        comp.warnings.append("Google client secret file missing.")
    if not token_ok:
        comp.warnings.append("Google token file missing — run OAuth flow first.")

    comp.details["status"] = "ready" if (cred_ok and token_ok) else "missing_auth"
    return comp


def _check_archive(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(
        name="archive",
        enabled=bool(config.brightspace_url),
    )

    if not config.brightspace_url:
        comp.details["status"] = "not_configured"
        return comp

    comp.configured = bool(
        config.brightspace_url
        and config.brightspace_username
        and config.brightspace_password
    )
    comp.details["url_configured"] = bool(config.brightspace_url)
    comp.details["credentials_configured"] = bool(
        config.brightspace_username and config.brightspace_password
    )

    if not _module_available("playwright"):
        comp.available = False
        msg = "playwright not installed (install with: pip install nexus-dashboard[archive])"
        if comp.configured:
            comp.errors.append(msg)
        else:
            comp.warnings.append(msg)
        comp.details["status"] = "dep_missing"
        return comp

    # Check Playwright browser binary
    try:
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
            comp.available = True
            comp.details["browser"] = "chromium_ok"
        except Exception as exc:
            comp.available = False
            msg = f"Playwright Chromium browser not installed. Run: playwright install chromium"
            if comp.configured:
                comp.errors.append(msg)
            else:
                comp.warnings.append(msg)
            comp.details["browser"] = f"error: {exc}"
    except ImportError:
        comp.available = False
        comp.errors.append("playwright import failed")

    comp.details["status"] = "ready" if comp.available else "incomplete"
    return comp


def _check_llm(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(
        name="llm",
        enabled=bool(config.llm_enabled),
    )

    comp.details["enabled_flag"] = config.llm_enabled
    comp.details["base_url_configured"] = bool(config.llm_base_url)
    comp.details["model_configured"] = bool(config.llm_model)

    if not config.llm_enabled:
        comp.details["status"] = "disabled"
        return comp

    comp.configured = bool(config.llm_api_key and config.llm_base_url and config.llm_model)
    comp.details["api_key_configured"] = bool(config.llm_api_key)

    if not comp.configured:
        comp.warnings.append("LLM enabled but API key, base URL, or model not configured — see .env.example")
        comp.details["status"] = "incomplete_config"
        return comp

    if not _module_available("litellm"):
        comp.available = False
        comp.errors.append(
            "litellm not installed (install with: pip install nexus-dashboard[llm])"
        )
        comp.details["status"] = "dep_missing"
        return comp

    # Optional: test connectivity
    comp.details["status"] = "configured"
    comp.available = True
    return comp


def _check_brightspace(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(name="brightspace", enabled=False)

    feeds_path = config.feeds_path
    if not feeds_path:
        comp.details["status"] = "no_feeds_path"
        return comp

    try:
        feeds = load_feeds(feeds_path)
    except (OSError, ValueError, TypeError) as exc:
        comp.errors.append(f"Unable to read feeds configuration: {exc}")
        comp.details["status"] = "feeds_unreadable"
        return comp

    enabled_feeds = [f for f in feeds if f.enabled]
    comp.enabled = len(enabled_feeds) > 0
    comp.details["total_feeds"] = len(feeds)
    comp.details["enabled_feeds"] = len(enabled_feeds)

    if not enabled_feeds:
        comp.details["status"] = "no_enabled_feeds"
        comp.warnings.append("No enabled Brightspace feeds found (data/feeds.json).")
    else:
        comp.configured = True
        comp.details["status"] = f"{len(enabled_feeds)} feeds enabled"

        # Check each enabled feed
        feed_statuses = []
        for feed in enabled_feeds:
            feed_statuses.append({
                "name": feed.name,
                "kind": feed.kind,
                "url": feed.url,
                "course": feed.course,
            })
        comp.details["feeds"] = feed_statuses

    return comp


def _check_canvas(config: AppConfig) -> ComponentStatus:
    comp = ComponentStatus(
        name="canvas",
        enabled=bool(config.canvas_url and config.canvas_token),
    )
    comp.details["url_configured"] = bool(config.canvas_url)
    comp.details["token_configured"] = bool(config.canvas_token)

    if not comp.enabled:
        comp.details["status"] = "not_configured"
        return comp

    comp.configured = True
    comp.available = _module_available("requests")
    comp.details["status"] = "configured" if comp.available else "dep_missing"
    return comp


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def run_doctor(config: AppConfig | None = None, include_google: bool = True) -> DoctorReport:
    config = config or default_config()
    failures: list[str] = []
    warnings: list[str] = []
    components: dict[str, ComponentStatus] = {}

    # Secret permission checks (cross-cutting)
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
            warnings.append(warning)

    # Run component checks
    component_checkers = [
        _check_core,
        _check_sqlite,
        _check_chroma,
        _check_google if include_google else lambda c: ComponentStatus(name="google", enabled=False, details={"status": "skipped"}),
        _check_archive,
        _check_llm,
        _check_brightspace,
        _check_canvas,
    ]

    for checker in component_checkers:
        comp = checker(config)
        components[comp.name] = comp
        failures.extend(comp.errors)
        warnings.extend(comp.warnings)

    # Overall verdict
    all_ok = len(failures) == 0

    # Build legacy-compatible details dict
    sqlite_comp = components.get("sqlite")
    chroma_comp = components.get("chroma")
    data_dir = Path(config.data_dir).expanduser()
    tasks_path = data_dir / "tasks.json"

    details: dict[str, object] = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "data_dir": str(config.data_dir),
        "data_dir_exists": data_dir.exists(),
        # legacy top-level keys
        "sqlite_path": str(sqlite_comp.details.get("path", "") if sqlite_comp else ""),
        "sqlite_status": sqlite_comp.details.get("status", "missing") if sqlite_comp else "missing",
        "sqlite_integrity": sqlite_comp.details.get("integrity") if sqlite_comp else None,
        "chroma_path": str(chroma_comp.details.get("path", "") if chroma_comp else ""),
        "chroma_status": chroma_comp.details.get("status", "missing") if chroma_comp else "missing",
        "tasks_path": str(tasks_path),
        "tasks_exists": tasks_path.exists(),
        "tasks_size": tasks_path.stat().st_size if tasks_path.exists() else 0,
        "feeds_path": str(config.feeds_path) if config.feeds_path else None,
        "feeds_enabled": (
            components.get("brightspace").details.get("enabled_feeds", 0)
            if components.get("brightspace") else 0
        ),
        "deps": {
            **({"requests": True, "feedparser": True, "icalendar": True}),
            **({"googleapiclient": _module_available("googleapiclient"),
                "google_auth_oauthlib": _module_available("google_auth_oauthlib"),
                "google_auth_httplib2": _module_available("google_auth_httplib2")}),
        },
        "llm": {
            "enabled": bool(config.llm_enabled),
            "configured": bool(config.llm_api_key),
            "base_url": bool(config.llm_base_url),
            "model": config.llm_model or "",
        },
        "secret_permissions": secret_permissions,
        "components": {
            name: {
                "enabled": c.enabled,
                "configured": c.configured,
                "available": c.available,
                "status": c.details.get("status", "unknown"),
                "details": c.details,
                "errors": c.errors,
                "warnings": c.warnings,
            }
            for name, c in components.items()
        },
    }

    return DoctorReport(
        ok=all_ok,
        failures=failures,
        warnings=warnings,
        details=details,
        components=components,
    )
