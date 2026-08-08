from __future__ import annotations

import errno
from pathlib import Path

import pytest

import nexus.knowledge.maintenance_lock as lock_module
from nexus.knowledge.maintenance_lock import (
    MaintenanceLockPlatformUnsupportedError,
    MaintenanceLockUnavailableError,
    document_ingest_lock,
    maintenance_lock,
)


@pytest.mark.parametrize("lock_kind", ["maintenance", "document"])
def test_maintenance_locks_report_unsupported_platform_without_creating_marker(
    tmp_path: Path, monkeypatch, lock_kind: str
) -> None:
    monkeypatch.setattr(lock_module, "fcntl", None)
    sqlite_path = tmp_path / "nexus.db"

    with pytest.raises(MaintenanceLockPlatformUnsupportedError):
        if lock_kind == "maintenance":
            with maintenance_lock(sqlite_path=sqlite_path, exclusive=True):
                raise AssertionError("unsupported lock must not yield")
        else:
            with document_ingest_lock(
                sqlite_path=sqlite_path,
                course_id="course",
                document_id="document",
            ):
                raise AssertionError("unsupported lock must not yield")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error_number",
    sorted({
        errno.ENOLCK,
        errno.ENOSYS,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
    }),
)
@pytest.mark.parametrize("lock_kind", ["maintenance", "document"])
def test_maintenance_locks_report_filesystem_lock_unavailable(
    tmp_path: Path, monkeypatch, error_number: int, lock_kind: str
) -> None:
    assert lock_module.fcntl is not None
    monkeypatch.setattr(
        lock_module.fcntl,
        "flock",
        lambda *_: (_ for _ in ()).throw(OSError(error_number, "unsupported")),
    )
    sqlite_path = tmp_path / "nexus.db"

    with pytest.raises(MaintenanceLockUnavailableError) as exc_info:
        if lock_kind == "maintenance":
            with maintenance_lock(sqlite_path=sqlite_path, exclusive=False):
                raise AssertionError("unavailable lock must not yield")
        else:
            with document_ingest_lock(
                sqlite_path=sqlite_path,
                course_id="course",
                document_id="document",
            ):
                raise AssertionError("unavailable lock must not yield")

    assert not isinstance(exc_info.value, MaintenanceLockPlatformUnsupportedError)
