"""Bounded, deterministic concurrency gate for a Nexus release candidate.

Run explicitly (the default pytest ``testpaths`` only includes ``tests``)::

    PYTHONPATH=src python -m pytest soak/test_concurrency_soak.py -q

Increase ``NEXUS_SOAK_ITERATIONS``, ``NEXUS_SOAK_WORKERS``, and
``NEXUS_SOAK_TIMEOUT_SECONDS`` for a longer local soak.  Defaults are kept
small enough for a sub-20-second CI gate.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import random
import sqlite3
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from nexus.io_utils import atomic_write_json
from nexus.knowledge.maintenance import create_backup, verify_backup
from nexus.knowledge.maintenance_lock import maintenance_lock
from nexus.sync_service import SyncBusyError, _sync_lock


SEED = 0x4E45585553
SQLITE_BUSY_TIMEOUT_MS = 2_000


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be positive, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {raw!r}")
    return value


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(db_path),
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
    )
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _initialize_database(db_path: Path) -> None:
    with _connect(db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        assert str(journal_mode).casefold() == "wal"
        conn.executescript(
            """
            CREATE TABLE parents (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE events (
                kind TEXT NOT NULL CHECK (kind IN ('ingest', 'qa')),
                worker INTEGER NOT NULL,
                iteration INTEGER NOT NULL,
                parent_id INTEGER NOT NULL REFERENCES parents(id),
                value INTEGER NOT NULL,
                PRIMARY KEY (kind, worker, iteration)
            );
            INSERT INTO parents(id) VALUES (1);
            """
        )


def _write_event(
    db_path: str,
    *,
    kind: str,
    worker: int,
    iteration: int,
    value: int,
) -> int:
    conn = _connect(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold() == "wal"
        observed = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO events(kind, worker, iteration, parent_id, value)
                VALUES (?, ?, ?, 1, ?)
                """,
                (kind, worker, iteration, value),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return observed
    finally:
        conn.close()


def _hold_shared_lock(
    db_path: str,
    chroma_path: str,
    ready: Any,
    release: Any,
    result_queue: Any,
    timeout: float,
) -> None:
    try:
        with maintenance_lock(
            sqlite_path=Path(db_path),
            chroma_path=Path(chroma_path),
            exclusive=False,
            timeout=timeout,
        ):
            ready.set()
            if not release.wait(timeout=timeout):
                raise TimeoutError("shared-lock release was not signalled")
        result_queue.put({"role": "shared-holder", "status": "ok"})
    except BaseException:
        result_queue.put(
            {"role": "shared-holder", "status": "error", "traceback": traceback.format_exc()}
        )
        raise


def _ingest_writer(
    db_path: str,
    chroma_path: str,
    status_path: str,
    worker: int,
    iterations: int,
    result_queue: Any,
    timeout: float,
) -> None:
    try:
        rng = random.Random(SEED + worker)
        last_value = -1
        for iteration in range(iterations):
            last_value = rng.randrange(0, 2**31)
            with maintenance_lock(
                sqlite_path=Path(db_path),
                chroma_path=Path(chroma_path),
                exclusive=False,
                timeout=timeout,
            ):
                observed = _write_event(
                    db_path,
                    kind="ingest",
                    worker=worker,
                    iteration=iteration,
                    value=last_value,
                )
                payload = {
                    "kind": "ingest",
                    "worker": worker,
                    "iteration": iteration,
                    "value": last_value,
                    "observed_rows": observed,
                }
                atomic_write_json(Path(status_path), payload)
                atomic_write_json(Path(chroma_path) / f"ingest-{worker}.json", payload)
        result_queue.put(
            {
                "role": "ingest",
                "status": "ok",
                "worker": worker,
                "writes": iterations,
                "last_value": last_value,
            }
        )
    except BaseException:
        result_queue.put(
            {
                "role": "ingest",
                "status": "error",
                "worker": worker,
                "traceback": traceback.format_exc(),
            }
        )
        raise


def _qa_writer(
    db_path: Path,
    chroma_path: Path,
    status_path: Path,
    worker: int,
    iterations: int,
    timeout: float,
) -> dict[str, Any]:
    rng = random.Random(SEED + 10_000 + worker)
    last_value = -1
    observed_rows = -1
    for iteration in range(iterations):
        last_value = rng.randrange(0, 2**31)
        with maintenance_lock(
            sqlite_path=db_path,
            chroma_path=chroma_path,
            exclusive=False,
            timeout=timeout,
        ):
            observed_rows = _write_event(
                str(db_path),
                kind="qa",
                worker=worker,
                iteration=iteration,
                value=last_value,
            )
            atomic_write_json(
                status_path,
                {
                    "kind": "qa",
                    "worker": worker,
                    "iteration": iteration,
                    "value": last_value,
                    "observed_rows": observed_rows,
                },
            )
    return {
        "role": "qa",
        "status": "ok",
        "worker": worker,
        "writes": iterations,
        "last_value": last_value,
        "observed_rows": observed_rows,
    }


def _create_backup_worker(
    db_path: str,
    chroma_path: str,
    archive_path: str,
    started: Any,
    finished: Any,
    result_queue: Any,
) -> None:
    try:
        started.set()
        report = create_backup(Path(db_path), Path(chroma_path), Path(archive_path))
        result_queue.put({"role": "backup", "status": "ok", "report": report})
    except BaseException:
        result_queue.put(
            {"role": "backup", "status": "error", "traceback": traceback.format_exc()}
        )
        raise
    finally:
        finished.set()


def _hold_sync_lock(
    lock_path: str,
    ready: Any,
    release: Any,
    result_queue: Any,
    timeout: float,
) -> None:
    try:
        with _sync_lock(Path(lock_path), run_id="sync-holder", trigger="soak"):
            ready.set()
            if not release.wait(timeout=timeout):
                raise TimeoutError("sync-lock release was not signalled")
        result_queue.put({"role": "sync-holder", "status": "ok"})
    except BaseException:
        result_queue.put(
            {"role": "sync-holder", "status": "error", "traceback": traceback.format_exc()}
        )
        raise


def _contend_for_sync(lock_path: Path, worker: int) -> dict[str, Any]:
    try:
        with _sync_lock(lock_path, run_id=f"sync-contender-{worker}", trigger="soak"):
            return {"worker": worker, "status": "acquired"}
    except SyncBusyError as exc:
        return {
            "worker": worker,
            "status": "busy",
            "owner_run_id": exc.owner.get("run_id"),
        }


def _remaining(deadline: float, operation: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"concurrency soak exceeded its deadline during {operation}")
    return remaining


def _join(process: multiprocessing.Process, deadline: float) -> None:
    process.join(timeout=_remaining(deadline, process.name))
    if process.is_alive():
        raise TimeoutError(f"{process.name} did not exit before the soak deadline")
    assert process.exitcode == 0, f"{process.name} exited with {process.exitcode}"


def _expected_rows(workers: int, iterations: int) -> list[tuple[str, int, int, int]]:
    rows: list[tuple[str, int, int, int]] = []
    for kind, offset in (("ingest", 0), ("qa", 10_000)):
        for worker in range(workers):
            rng = random.Random(SEED + offset + worker)
            for iteration in range(iterations):
                rows.append((kind, worker, iteration, rng.randrange(0, 2**31)))
    return sorted(rows)


def _last_random_value(seed: int, iterations: int) -> int:
    rng = random.Random(seed)
    value = -1
    for _ in range(iterations):
        value = rng.randrange(0, 2**31)
    return value


def _assert_database(db_path: Path, expected: list[tuple[str, int, int, int]]) -> None:
    with _connect(db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).casefold() == "wal"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = conn.execute(
            "SELECT kind, worker, iteration, value FROM events "
            "ORDER BY kind, worker, iteration"
        ).fetchall()
    assert rows == expected


@pytest.mark.skipif(os.name != "posix", reason="release soak requires POSIX flock")
def test_concurrent_ingest_qa_backup_and_sync_leave_consistent_release_state(
    tmp_path: Path,
) -> None:
    iterations = _positive_int_env("NEXUS_SOAK_ITERATIONS", 8)
    workers = _positive_int_env("NEXUS_SOAK_WORKERS", 2)
    timeout = _positive_float_env("NEXUS_SOAK_TIMEOUT_SECONDS", 15.0)
    deadline = time.monotonic() + timeout

    root = tmp_path / "runtime"
    chroma_path = root / "chroma"
    status_dir = root / "status"
    db_path = root / "nexus.db"
    archive_path = root / "backups" / "concurrent.zip"
    sync_lock_path = root / ".sync.lock"
    chroma_path.mkdir(parents=True)
    status_dir.mkdir()
    _initialize_database(db_path)

    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    shared_ready = context.Event()
    shared_release = context.Event()
    backup_started = context.Event()
    backup_finished = context.Event()
    sync_ready = context.Event()
    sync_release = context.Event()

    shared_holder = context.Process(
        name="soak-shared-holder",
        target=_hold_shared_lock,
        args=(
            str(db_path), str(chroma_path), shared_ready, shared_release,
            results, timeout,
        ),
    )
    backup = context.Process(
        name="soak-exclusive-backup",
        target=_create_backup_worker,
        args=(
            str(db_path), str(chroma_path), str(archive_path),
            backup_started, backup_finished, results,
        ),
    )
    sync_holder = context.Process(
        name="soak-sync-holder",
        target=_hold_sync_lock,
        args=(str(sync_lock_path), sync_ready, sync_release, results, timeout),
    )
    ingest_processes = [
        context.Process(
            name=f"soak-ingest-{worker}",
            target=_ingest_writer,
            args=(
                str(db_path), str(chroma_path), str(status_dir / "ingest.json"),
                worker, iterations, results, timeout,
            ),
        )
        for worker in range(workers)
    ]
    processes = [shared_holder, backup, sync_holder, *ingest_processes]

    try:
        shared_holder.start()
        assert shared_ready.wait(timeout=_remaining(deadline, "shared lock acquisition"))

        backup.start()
        assert backup_started.wait(timeout=_remaining(deadline, "backup start"))
        assert backup_finished.wait(timeout=0.1) is False

        sync_holder.start()
        assert sync_ready.wait(timeout=_remaining(deadline, "sync lock acquisition"))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            sync_futures = [
                executor.submit(_contend_for_sync, sync_lock_path, worker)
                for worker in range(workers)
            ]
            sync_results = [
                future.result(timeout=_remaining(deadline, "sync contention"))
                for future in sync_futures
            ]
        assert sync_results == [
            {"worker": worker, "status": "busy", "owner_run_id": "sync-holder"}
            for worker in range(workers)
        ]
        sync_release.set()
        _join(sync_holder, deadline)
        with _sync_lock(sync_lock_path, run_id="sync-successor", trigger="soak"):
            owner = json.loads(sync_lock_path.read_text(encoding="utf-8"))
            assert owner["run_id"] == "sync-successor"
        assert not sync_lock_path.exists()

        for process in ingest_processes:
            process.start()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            qa_futures = [
                executor.submit(
                    _qa_writer,
                    db_path,
                    chroma_path,
                    status_dir / "qa.json",
                    worker,
                    iterations,
                    timeout,
                )
                for worker in range(workers)
            ]
            qa_results = [
                future.result(timeout=_remaining(deadline, "QA WAL workload"))
                for future in qa_futures
            ]
        assert qa_results == [
            {
                "role": "qa",
                "status": "ok",
                "worker": worker,
                "writes": iterations,
                "last_value": _last_random_value(SEED + 10_000 + worker, iterations),
                "observed_rows": qa_results[worker]["observed_rows"],
            }
            for worker in range(workers)
        ]
        assert all(result["observed_rows"] >= 0 for result in qa_results)

        for process in ingest_processes:
            _join(process, deadline)

        # The exclusive backup has been waiting behind a real shared flock
        # throughout all writes.  Releasing the holder lets it snapshot the
        # complete, quiescent state.
        assert backup_finished.is_set() is False
        shared_release.set()
        _join(shared_holder, deadline)
        _join(backup, deadline)

        expected_process_results = workers + 3
        process_results = [
            results.get(timeout=_remaining(deadline, "worker result collection"))
            for _ in range(expected_process_results)
        ]
        assert all(result["status"] == "ok" for result in process_results), process_results
        assert sorted(result["role"] for result in process_results) == sorted(
            ["backup", "shared-holder", "sync-holder", *(["ingest"] * workers)]
        )

        expected_rows = _expected_rows(workers, iterations)
        _assert_database(db_path, expected_rows)

        verification = verify_backup(archive_path)
        assert verification == {
            "ok": True,
            "archive": str(archive_path.resolve()),
            "files": workers + 1,
            "verified": workers + 1,
            "failed": [],
        }
        extracted = tmp_path / "verified-backup"
        with zipfile.ZipFile(archive_path) as archive:
            archive.extract("sqlite/nexus.db", extracted)
        _assert_database(extracted / "sqlite" / "nexus.db", expected_rows)

        json_paths = sorted(root.rglob("*.json"))
        assert json_paths == sorted(
            [
                status_dir / "ingest.json",
                status_dir / "qa.json",
                *[chroma_path / f"ingest-{worker}.json" for worker in range(workers)],
            ]
        )
        for path in json_paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(payload, dict)
            assert payload["kind"] in {"ingest", "qa"}
            assert isinstance(payload["worker"], int)
            assert 0 <= payload["worker"] < workers
            assert isinstance(payload["iteration"], int)
            assert 0 <= payload["iteration"] < iterations
            assert isinstance(payload["value"], int)
            assert isinstance(payload["observed_rows"], int)

        with zipfile.ZipFile(archive_path) as archive:
            for worker in range(workers):
                live_payload = json.loads(
                    (chroma_path / f"ingest-{worker}.json").read_text(encoding="utf-8")
                )
                assert live_payload["kind"] == "ingest"
                assert live_payload["worker"] == worker
                assert live_payload["iteration"] == iterations - 1
                assert live_payload["value"] == _last_random_value(
                    SEED + worker, iterations
                )
                assert json.loads(archive.read(f"chroma/ingest-{worker}.json")) == live_payload

        assert [
            path for path in root.rglob("*")
            if path.name.endswith(".tmp")
        ] == []
        assert not sync_lock_path.exists()
    finally:
        shared_release.set()
        sync_release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            if process.pid is not None:
                process.join(timeout=1)
        results.cancel_join_thread()
        results.close()
