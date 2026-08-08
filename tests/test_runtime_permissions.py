from __future__ import annotations

from pathlib import Path
import stat

import pytest

from nexus.knowledge.sqlite_store import SQLiteStore, connect
from nexus.storage import save_tasks


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_sqlite_store_creates_private_parent_and_database(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime" / "nexus.db"

    SQLiteStore(db_path)

    assert _mode(db_path.parent) == 0o700
    assert _mode(db_path) == 0o600


def test_storage_creates_owner_only_runtime_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "runtime"

    save_tasks([], data_dir / "tasks.json")

    assert _mode(data_dir) == 0o700


def test_sqlite_store_restricts_existing_database_but_not_parent(tmp_path: Path) -> None:
    data_dir = tmp_path / "shared-runtime"
    data_dir.mkdir(mode=0o750)
    data_dir.chmod(0o750)
    db_path = data_dir / "nexus.db"
    SQLiteStore(db_path)
    db_path.chmod(0o644)

    SQLiteStore(db_path)

    assert _mode(data_dir) == 0o750
    assert _mode(db_path) == 0o600


def test_direct_connect_creates_owner_only_database(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime" / "direct.db"

    with connect(db_path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

    assert _mode(db_path.parent) == 0o700
    assert _mode(db_path) == 0o600


def test_sqlite_store_rejects_symlink_without_chmodding_target(tmp_path: Path) -> None:
    target = tmp_path / "target.db"
    SQLiteStore(target)
    target.chmod(0o644)
    link = tmp_path / "linked.db"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="must not be a symlink"):
        SQLiteStore(link)

    assert _mode(target) == 0o644
