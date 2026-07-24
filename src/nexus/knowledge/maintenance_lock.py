"""Cross-process coordination between ingestion and storage maintenance."""
from __future__ import annotations

import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class MaintenanceLockTimeout(TimeoutError):
    """Raised when a maintenance lock cannot be acquired before the deadline."""


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
    lock_path = document_ingest_lock_path(
        sqlite_path=sqlite_path,
        chroma_path=chroma_path,
        course_id=course_id,
        document_id=document_id,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
            fcntl.flock(descriptor, fcntl.LOCK_UN)
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
    lock_path = maintenance_lock_path(
        sqlite_path=sqlite_path, chroma_path=chroma_path
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
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
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
