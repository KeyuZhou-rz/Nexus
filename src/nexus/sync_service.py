"""Single-flight, auditable synchronization service for CLI, schedulers, and UI."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import socket
from typing import Any, Iterator
from uuid import uuid4

try:  # ``fcntl`` is not available on Windows and some constrained runtimes.
    import fcntl
except ImportError:  # pragma: no cover - exercised by monkeypatch on POSIX CI
    fcntl = None  # type: ignore[assignment]

from .config import default_config, ensure_private_runtime_dir
from .context_api import build_context
from .io_utils import atomic_write_json
from .pipeline import run_mvp_pipeline


SYNC_LOCK = ".sync.lock"
SYNC_RECEIPT = "sync_receipt.json"
LOCK_STALE_AFTER_SECONDS = 3600
LOCK_GUARD_SUFFIX = ".acquire"
LOCK_PROTOCOL = "flock-v1"
LOCK_UNAVAILABLE_ERRNOS = frozenset(
    value
    for value in (
        getattr(errno, "ENOLCK", None),
        getattr(errno, "ENOSYS", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if value is not None
)


class SyncBusyError(RuntimeError):
    def __init__(self, owner: dict[str, Any]) -> None:
        super().__init__("Another sync is already running")
        self.owner = owner


class SyncPlatformUnsupportedError(RuntimeError):
    """Raised when the runtime has no safe cross-process lock primitive."""


class SyncLockUnavailableError(RuntimeError):
    """Raised when the filesystem cannot provide the required process lock."""


def run_sync(
    *,
    data_dir: Path | None = None,
    trigger: str = "cli",
    include_archive: bool = False,
    include_google: bool = True,
    archive_timeout: int = 420,
) -> dict[str, Any]:
    """Run one non-interactive sync and persist a privacy-bounded receipt."""
    root = Path(data_dir or default_config().data_dir).expanduser()
    started = datetime.now(timezone.utc)
    run_id = uuid4().hex

    try:
        ensure_private_runtime_dir(root)
        with _sync_lock(root / SYNC_LOCK, run_id=run_id, trigger=trigger):
            report = run_mvp_pipeline(
                data_dir=root,
                run_archive_sync=include_archive,
                archive_timeout=archive_timeout,
                include_google=include_google,
                use_llm=False,
            )
            context = build_context(data_dir=root)
            receipt = _build_receipt(
                run_id=run_id,
                trigger=trigger,
                started=started,
                report=report,
                context=context,
            )
            atomic_write_json(root / SYNC_RECEIPT, receipt)
            return receipt
    except SyncBusyError as exc:
        return {
            "schema_version": "1.0",
            "ok": False,
            "status": "busy",
            "run_id": run_id,
            "trigger": trigger,
            "data_dir": str(root),
            "owner": _public_owner(exc.owner),
        }
    except Exception as exc:
        finished = datetime.now(timezone.utc)
        receipt = {
            "schema_version": "1.0",
            "ok": False,
            "status": "failed",
            "run_id": run_id,
            "trigger": trigger,
            "data_dir": str(root),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "error_type": type(exc).__name__,
        }
        try:
            atomic_write_json(root / SYNC_RECEIPT, receipt)
        except Exception:
            # The receipt is best-effort on storage failures. The caller still
            # receives a stable, privacy-bounded failure document.
            pass
        return receipt


def load_sync_receipt(data_dir: Path) -> dict[str, Any]:
    path = Path(data_dir).expanduser() / SYNC_RECEIPT
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_receipt(
    *,
    run_id: str,
    trigger: str,
    started: datetime,
    report: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    finished = datetime.now(timezone.utc)
    learning = context.get("learning") if isinstance(context.get("learning"), dict) else {}
    focus = learning.get("focus") if isinstance(learning.get("focus"), list) else []
    linked_focus = sum(
        1 for item in focus if isinstance(item, dict) and isinstance(item.get("related_task"), dict)
    )
    raw_sources = context.get("sync", {}).get("sources", {})
    sources = _public_sources(raw_sources)
    task_count = context.get("tasks", {}).get("count", 0)
    evidence = learning.get("evidence") if isinstance(learning.get("evidence"), list) else []
    return {
        "schema_version": "1.0",
        "ok": report.get("ok") is True,
        "status": "succeeded" if report.get("ok") is True else "failed",
        "run_id": run_id,
        "trigger": trigger,
        "data_dir": str(context.get("scope", {}).get("data_dir") or ""),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "pipeline_report": str(report.get("report_path") or ""),
        "sources": sources if isinstance(sources, dict) else {},
        "summary": {
            "task_count": int(task_count) if isinstance(task_count, int) else 0,
            "learning_available": learning.get("available") is True,
            "evidence_count": len(evidence),
            "focus_count": len(focus),
            "linked_focus_count": linked_focus,
        },
    }


def _public_sources(raw_sources: Any) -> dict[str, dict[str, Any]]:
    """Keep receipts free of provider error text and tokenized URLs."""
    if not isinstance(raw_sources, dict):
        return {}
    allowed = {
        "source", "enabled", "configured", "status", "freshness",
        "last_success_at", "item_count",
    }
    result: dict[str, dict[str, Any]] = {}
    for name, value in raw_sources.items():
        if isinstance(value, dict):
            result[str(name)] = {key: value.get(key) for key in allowed if key in value}
    return result


@contextmanager
def _sync_lock(path: Path, *, run_id: str, trigger: str) -> Iterator[None]:
    owner = {
        "run_id": run_id,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "trigger": trigger,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "lock_protocol": LOCK_PROTOCOL,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    # The sidecar flock is the authoritative single-flight primitive and stays
    # held through the whole sync. The JSON marker is owner metadata plus a
    # compatibility path for locks left by versions that predate the flock.
    with _sync_lifetime_guard(path):
        acquired = _create_lock(path, owner)
        if not acquired:
            existing = _read_lock(path)
            if _owner_is_gone(path, existing):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                acquired = _create_lock(path, owner)
            if not acquired:
                # A lock can be replaced in the unlink/create gap by a process
                # that did not observe the old inode. Report that current owner
                # and, critically, leave its lock untouched.
                current = _read_lock(path)
                raise SyncBusyError(current or existing)

        try:
            yield
        finally:
            # Verification and removal happen while the lifetime flock is
            # still held, so no cooperative successor can appear between them.
            current = _read_lock(path)
            if current.get("run_id") == run_id:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


@contextmanager
def _sync_lifetime_guard(path: Path) -> Iterator[None]:
    """Hold the authoritative process lock for one complete sync context."""
    if fcntl is None:
        raise SyncPlatformUnsupportedError(
            "Single-flight sync requires POSIX fcntl.flock support"
        )
    guard_path = path.with_name(f"{path.name}{LOCK_GUARD_SUFFIX}")
    descriptor = os.open(guard_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        try:
            _flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncBusyError(_read_lock(path)) from None
        locked = True
        yield
    finally:
        try:
            if locked:
                _flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _flock(descriptor: int, operation: int) -> None:
    """Apply flock without ever degrading to an unsafe marker-only lock."""
    if fcntl is None:
        raise SyncPlatformUnsupportedError(
            "Single-flight sync requires POSIX fcntl.flock support"
        )
    try:
        fcntl.flock(descriptor, operation)
    except OSError as exc:
        if exc.errno in LOCK_UNAVAILABLE_ERRNOS:
            raise SyncLockUnavailableError(
                "The data filesystem does not provide required flock semantics"
            ) from exc
        raise


def _create_lock(path: Path, owner: dict[str, Any]) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(owner, handle, ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _owner_is_gone(path: Path, owner: dict[str, Any]) -> bool:
    try:
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    except OSError:
        return False
    if owner.get("lock_protocol") == LOCK_PROTOCOL:
        # This function is called only after acquiring the lifetime guard. A
        # same-protocol marker without a flock holder is therefore abandoned,
        # even if its numeric PID has since been reused.
        return True
    pid = owner.get("pid")
    host = owner.get("host")
    local_host = socket.gethostname()
    if host and host != local_host:
        # A legacy owner on another host cannot be proven dead locally. Its
        # age alone is not sufficient evidence to permit a second sync.
        return False
    if host == local_host and type(pid) is int and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except (PermissionError, OSError, OverflowError):
            # Permission failures and ambiguous OS errors do not prove death.
            return False
        else:
            return False

    # Retain a bounded recovery path for old corrupt/partial local markers. New
    # writers are protected by the lifetime flock even before metadata exists.
    return age > LOCK_STALE_AFTER_SECONDS


def _public_owner(owner: dict[str, Any]) -> dict[str, Any]:
    return {
        key: owner.get(key)
        for key in ("pid", "trigger", "started_at")
        if owner.get(key) is not None
    }
