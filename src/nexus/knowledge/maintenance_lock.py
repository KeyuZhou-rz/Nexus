"""Cross-process coordination between ingestion and storage maintenance."""
from __future__ import annotations

import errno
import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:  # ``fcntl`` is absent on Windows and some constrained Python runtimes.
    import fcntl
except ImportError:  # pragma: no cover - exercised by monkeypatch on POSIX CI
    fcntl = None  # type: ignore[assignment]


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


class MaintenanceLockTimeout(TimeoutError):
    """Raised when a maintenance lock cannot be acquired before the deadline."""


class MaintenanceLockUnavailableError(RuntimeError):
    """Raised when safe cross-process maintenance locking is unavailable."""


class MaintenanceLockPlatformUnsupportedError(MaintenanceLockUnavailableError):
    """Raised when the runtime does not expose POSIX flock support."""


def maintenance_lock_path(
    *, sqlite_path: Path | None = None, chroma_path: Path | None = None
) -> Path:
    if sqlite_path is not None:
        root = sqlite_path.expanduser().resolve().parent
    elif chroma_path is not None:
        root = chroma_path.expanduser().resolve().parent
    else:
        raise ValueError("sqlite_path or chroma_path is required")
    return root / ".nexus-maintenance.lock"


def document_ingest_lock_path(
    *,
    sqlite_path: Path | None = None,
    chroma_path: Path | None = None,
    course_id: str,
    document_id: str,
) -> Path:
    """Return a stable, private lock path for one logical course document."""
    root = maintenance_lock_path(
        sqlite_path=sqlite_path, chroma_path=chroma_path
    ).parent
    identity = f"{course_id}\0{document_id}".encode("utf-8")
    return root / f".nexus-ingest-{hashlib.sha256(identity).hexdigest()}.lock"


@contextmanager
def document_ingest_lock(
    *,
    sqlite_path: Path | None = None,
    chroma_path: Path | None = None,
    course_id: str,
    document_id: str,
    timeout: float = 30.0,
) -> Iterator[Path]:
    """Lease exclusive ingestion access to one course/document identity.

    Unlike the shared maintenance lock, this lock intentionally permits
    unrelated documents to ingest concurrently while preventing a second
    process from deleting or rewriting the same document mid-ingest.
    """
    if timeout < 0:
        raise ValueError("timeout must not be negative")
    lock_api = _require_lock_api()
    lock_path = document_ingest_lock_path(
        sqlite_path=sqlite_path,
        chroma_path=chroma_path,
        course_id=course_id,
        document_id=document_id,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                _flock(descriptor, lock_api.LOCK_EX | lock_api.LOCK_NB)
                locked = True
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise MaintenanceLockTimeout(
                        f"Timed out acquiring document ingest lock: {lock_path}"
                    ) from exc
                time.sleep(0.01)
        yield lock_path
    finally:
        try:
            if locked:
                _flock(descriptor, lock_api.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def maintenance_lock(
    *,
    sqlite_path: Path | None = None,
    chroma_path: Path | None = None,
    exclusive: bool,
    timeout: float = 30.0,
) -> Iterator[Path]:
    """Acquire a shared ingest lock or exclusive maintenance lock."""
    if timeout < 0:
        raise ValueError("timeout must not be negative")
    lock_api = _require_lock_api()
    lock_path = maintenance_lock_path(
        sqlite_path=sqlite_path, chroma_path=chroma_path
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    operation = lock_api.LOCK_EX if exclusive else lock_api.LOCK_SH
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                _flock(descriptor, operation | lock_api.LOCK_NB)
                locked = True
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    mode = "exclusive" if exclusive else "shared"
                    raise MaintenanceLockTimeout(
                        f"Timed out acquiring {mode} maintenance lock: {lock_path}"
                    ) from exc
                time.sleep(0.01)
        yield lock_path
    finally:
        try:
            if locked:
                _flock(descriptor, lock_api.LOCK_UN)
        finally:
            os.close(descriptor)


def _require_lock_api():
    if fcntl is None:
        raise MaintenanceLockPlatformUnsupportedError(
            "Maintenance locking requires POSIX fcntl.flock support"
        )
    return fcntl


def _flock(descriptor: int, operation: int) -> None:
    lock_api = _require_lock_api()
    try:
        lock_api.flock(descriptor, operation)
    except OSError as exc:
        if exc.errno in LOCK_UNAVAILABLE_ERRNOS:
            raise MaintenanceLockUnavailableError(
                "The data filesystem does not provide required flock semantics"
            ) from exc
        raise
