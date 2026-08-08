from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any

import pytest

from nexus.sync_service import (
    LOCK_PROTOCOL,
    LOCK_STALE_AFTER_SECONDS,
    SyncBusyError,
    _sync_lock,
    load_sync_receipt,
    run_sync,
)


def _hold_sync_lock_worker(
    lock_path: str,
    ready: Any,
    release: Any,
    *,
    crash: bool = False,
) -> None:
    with _sync_lock(Path(lock_path), run_id="holder-run", trigger="multiprocess-test"):
        ready.set()
        if not release.wait(timeout=10):
            raise TimeoutError("parent did not release holder")
        if crash:
            os._exit(17)


def _attempt_sync_lock_worker(lock_path: str, results: Any) -> None:
    started = time.monotonic()
    try:
        with _sync_lock(Path(lock_path), run_id="contender-run", trigger="multiprocess-test"):
            result = {"status": "acquired"}
    except SyncBusyError as exc:
        result = {"status": "busy", "owner": exc.owner}
    result["elapsed"] = time.monotonic() - started
    results.put(result)


def _context() -> dict:
    return {
        "scope": {"data_dir": "/tmp/runtime"},
        "tasks": {"count": 4},
        "sync": {"sources": {"brightspace": {"status": "succeeded"}}},
        "learning": {
            "available": True,
            "evidence": [{"source": "quiz"}],
            "focus": [{"related_task": {"id": "task-1"}}, {"related_task": None}],
        },
    }


def test_run_sync_writes_privacy_bounded_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "nexus.sync_service.run_mvp_pipeline",
        lambda **_: {"ok": True, "report_path": str(tmp_path / "pipeline_report.json")},
    )
    monkeypatch.setattr("nexus.sync_service.build_context", lambda **_: _context())

    receipt = run_sync(data_dir=tmp_path, trigger="test")

    assert receipt["ok"] is True
    assert receipt["status"] == "succeeded"
    assert receipt["summary"] == {
        "task_count": 4,
        "learning_available": True,
        "evidence_count": 1,
        "focus_count": 2,
        "linked_focus_count": 1,
    }
    saved = json.loads((tmp_path / "sync_receipt.json").read_text(encoding="utf-8"))
    assert saved == receipt
    assert not (tmp_path / ".sync.lock").exists()
    assert "question" not in json.dumps(receipt)


def test_sync_receipt_omits_provider_error_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "nexus.sync_service.run_mvp_pipeline",
        lambda **_: {"ok": False, "report_path": str(tmp_path / "pipeline_report.json")},
    )
    monkeypatch.setattr(
        "nexus.sync_service.build_context",
        lambda **_: {
            **_context(),
            "sync": {"sources": {"brightspace": {
                "status": "failed",
                "error": "https://example.invalid/feed?token=secret",
                "item_count": 0,
            }}},
        },
    )

    receipt = run_sync(data_dir=tmp_path)

    assert "secret" not in json.dumps(receipt)
    assert "error" not in receipt["sources"]["brightspace"]


def test_run_sync_returns_busy_without_overwriting_existing_receipt(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "sync_receipt.json").write_text('{"status":"succeeded"}', encoding="utf-8")
    with _sync_lock(tmp_path / ".sync.lock", run_id="other", trigger="scheduler"):
        monkeypatch.setattr(
            "nexus.sync_service.run_mvp_pipeline",
            lambda **_: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        receipt = run_sync(data_dir=tmp_path, trigger="dashboard")

    assert receipt["status"] == "busy"
    assert set(receipt["owner"]) == {"pid", "trigger", "started_at"}
    assert "run_id" not in receipt["owner"]
    assert "host" not in receipt["owner"]
    assert json.loads((tmp_path / "sync_receipt.json").read_text(encoding="utf-8")) == {
        "status": "succeeded"
    }


def test_run_sync_records_failure_without_leaking_exception_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "nexus.sync_service.run_mvp_pipeline",
        lambda **_: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )

    receipt = run_sync(data_dir=tmp_path)

    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "RuntimeError"
    assert "secret" not in json.dumps(receipt)
    assert not (tmp_path / ".sync.lock").exists()


@pytest.mark.parametrize("content", [b"\xffnot-json", b"[]"])
def test_load_sync_receipt_rejects_malformed_or_non_object_payload(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "sync_receipt.json").write_bytes(content)

    assert load_sync_receipt(tmp_path) == {}


def test_sync_lock_reclaims_dead_local_owner(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / ".sync.lock"
    dead_pid = 987_654_321
    lock_path.write_text(
        json.dumps({
            "run_id": "dead-run",
            "pid": dead_pid,
            "host": socket.gethostname(),
            "trigger": "scheduler",
        }),
        encoding="utf-8",
    )

    def report_dead(pid: int, signal: int) -> None:
        assert (pid, signal) == (dead_pid, 0)
        raise ProcessLookupError

    monkeypatch.setattr("nexus.sync_service.os.kill", report_dead)

    with _sync_lock(lock_path, run_id="replacement", trigger="test"):
        assert json.loads(lock_path.read_text(encoding="utf-8"))["run_id"] == "replacement"

    assert not lock_path.exists()


def test_sync_lock_reclaims_old_corrupt_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / ".sync.lock"
    lock_path.write_bytes(b"\xffnot-json")
    stale_time = lock_path.stat().st_mtime - LOCK_STALE_AFTER_SECONDS - 1
    os.utime(lock_path, (stale_time, stale_time))

    with _sync_lock(lock_path, run_id="replacement", trigger="test"):
        assert json.loads(lock_path.read_text(encoding="utf-8"))["run_id"] == "replacement"

    assert not lock_path.exists()


def test_sync_lock_does_not_reclaim_recent_foreign_host_owner(tmp_path: Path, monkeypatch) -> None:
    lock_path = tmp_path / ".sync.lock"
    foreign_owner = {
        "run_id": "remote-run",
        "pid": 999_999,
        "host": "other-host.invalid",
        "trigger": "scheduler",
        "started_at": "2026-08-08T00:00:00+00:00",
    }
    lock_path.write_text(json.dumps(foreign_owner), encoding="utf-8")
    monkeypatch.setattr(
        "nexus.sync_service.os.kill",
        lambda *_: (_ for _ in ()).throw(AssertionError("foreign PID must not be probed")),
    )

    with pytest.raises(SyncBusyError) as exc_info:
        with _sync_lock(lock_path, run_id="local-run", trigger="test"):
            raise AssertionError("foreign lock must remain busy")

    assert exc_info.value.owner == foreign_owner
    assert json.loads(lock_path.read_text(encoding="utf-8")) == foreign_owner


def test_sync_lock_does_not_reclaim_expired_foreign_host_owner(
    tmp_path: Path, monkeypatch
) -> None:
    lock_path = tmp_path / ".sync.lock"
    foreign_owner = {
        "run_id": "remote-run",
        "pid": 999_999,
        "host": "other-host.invalid",
        "trigger": "scheduler",
    }
    lock_path.write_text(json.dumps(foreign_owner), encoding="utf-8")
    stale_time = lock_path.stat().st_mtime - LOCK_STALE_AFTER_SECONDS - 1
    os.utime(lock_path, (stale_time, stale_time))
    monkeypatch.setattr(
        "nexus.sync_service.os.kill",
        lambda *_: (_ for _ in ()).throw(AssertionError("foreign PID must not be probed")),
    )

    with pytest.raises(SyncBusyError) as exc_info:
        with _sync_lock(lock_path, run_id="replacement", trigger="test"):
            raise AssertionError("foreign owner cannot be proven dead from TTL")

    assert exc_info.value.owner == foreign_owner
    assert json.loads(lock_path.read_text(encoding="utf-8")) == foreign_owner


def test_sync_lock_does_not_reclaim_expired_local_owner_when_pid_is_alive(
    tmp_path: Path, monkeypatch
) -> None:
    lock_path = tmp_path / ".sync.lock"
    active_owner = {
        "run_id": "long-running",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "trigger": "scheduler",
    }
    lock_path.write_text(json.dumps(active_owner), encoding="utf-8")
    stale_time = lock_path.stat().st_mtime - LOCK_STALE_AFTER_SECONDS - 1
    os.utime(lock_path, (stale_time, stale_time))
    probes: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "nexus.sync_service.os.kill",
        lambda pid, signal: probes.append((pid, signal)),
    )

    with pytest.raises(SyncBusyError) as exc_info:
        with _sync_lock(lock_path, run_id="replacement", trigger="test"):
            raise AssertionError("active local owner must remain busy past TTL")

    assert probes == [(os.getpid(), 0)]
    assert exc_info.value.owner == active_owner
    assert json.loads(lock_path.read_text(encoding="utf-8")) == active_owner


def test_sync_lock_does_not_reclaim_local_owner_when_pid_probe_is_denied(
    tmp_path: Path, monkeypatch
) -> None:
    lock_path = tmp_path / ".sync.lock"
    owner = {
        "run_id": "permission-denied",
        "pid": 12345,
        "host": socket.gethostname(),
        "trigger": "scheduler",
    }
    lock_path.write_text(json.dumps(owner), encoding="utf-8")
    stale_time = lock_path.stat().st_mtime - LOCK_STALE_AFTER_SECONDS - 1
    os.utime(lock_path, (stale_time, stale_time))
    monkeypatch.setattr(
        "nexus.sync_service.os.kill",
        lambda *_: (_ for _ in ()).throw(PermissionError()),
    )

    with pytest.raises(SyncBusyError) as exc_info:
        with _sync_lock(lock_path, run_id="replacement", trigger="test"):
            raise AssertionError("permission failure does not prove owner death")

    assert exc_info.value.owner == owner
    assert json.loads(lock_path.read_text(encoding="utf-8")) == owner


def test_sync_lock_replaces_abandoned_same_protocol_marker_despite_pid_reuse(
    tmp_path: Path, monkeypatch
) -> None:
    lock_path = tmp_path / ".sync.lock"
    lock_path.write_text(
        json.dumps({
            "run_id": "crashed-run",
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "trigger": "scheduler",
            "lock_protocol": LOCK_PROTOCOL,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nexus.sync_service.os.kill",
        lambda *_: (_ for _ in ()).throw(AssertionError("same protocol needs no PID probe")),
    )

    with _sync_lock(lock_path, run_id="replacement", trigger="test"):
        current = json.loads(lock_path.read_text(encoding="utf-8"))
        assert current["run_id"] == "replacement"
        assert current["lock_protocol"] == LOCK_PROTOCOL

    assert not lock_path.exists()


def test_lifetime_lock_makes_second_process_busy_immediately(tmp_path: Path) -> None:
    lock_path = tmp_path / ".sync.lock"
    process_context = multiprocessing.get_context("spawn")
    holder_ready = process_context.Event()
    release_holder = process_context.Event()
    results = process_context.Queue()
    holder = process_context.Process(
        target=_hold_sync_lock_worker,
        args=(str(lock_path), holder_ready, release_holder),
    )
    contender = process_context.Process(
        target=_attempt_sync_lock_worker,
        args=(str(lock_path), results),
    )

    holder.start()
    try:
        assert holder_ready.wait(timeout=5)
        contender.start()
        result = results.get(timeout=5)
        contender.join(timeout=5)

        assert not contender.is_alive()
        assert contender.exitcode == 0
        assert result["status"] == "busy"
        assert result["owner"]["run_id"] == "holder-run"
        assert result["elapsed"] < 1.0
        assert json.loads(lock_path.read_text(encoding="utf-8"))["run_id"] == "holder-run"
    finally:
        release_holder.set()
        holder.join(timeout=5)
        if contender.is_alive():
            contender.terminate()
            contender.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)

    assert holder.exitcode == 0
    assert not lock_path.exists()


def test_lifetime_lock_makes_second_thread_in_same_process_busy(tmp_path: Path) -> None:
    lock_path = tmp_path / ".sync.lock"
    outcomes: list[str] = []

    def contend() -> None:
        try:
            with _sync_lock(lock_path, run_id="thread-contender", trigger="test"):
                outcomes.append("acquired")
        except SyncBusyError:
            outcomes.append("busy")

    with _sync_lock(lock_path, run_id="thread-holder", trigger="test"):
        contender = threading.Thread(target=contend)
        contender.start()
        contender.join(timeout=2)
        assert not contender.is_alive()
        assert outcomes == ["busy"]

    assert not lock_path.exists()


def test_process_crash_releases_lifetime_lock_for_successor(tmp_path: Path) -> None:
    lock_path = tmp_path / ".sync.lock"
    process_context = multiprocessing.get_context("spawn")
    holder_ready = process_context.Event()
    crash_holder = process_context.Event()
    results = process_context.Queue()
    holder = process_context.Process(
        target=_hold_sync_lock_worker,
        args=(str(lock_path), holder_ready, crash_holder),
        kwargs={"crash": True},
    )
    successor = process_context.Process(
        target=_attempt_sync_lock_worker,
        args=(str(lock_path), results),
    )

    holder.start()
    try:
        assert holder_ready.wait(timeout=5)
        assert json.loads(lock_path.read_text(encoding="utf-8"))["run_id"] == "holder-run"
        crash_holder.set()
        holder.join(timeout=5)
        assert holder.exitcode == 17

        successor.start()
        result = results.get(timeout=5)
        successor.join(timeout=5)
        assert successor.exitcode == 0
        assert result["status"] == "acquired"
    finally:
        crash_holder.set()
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
        if successor.is_alive():
            successor.terminate()
            successor.join(timeout=5)

    assert not lock_path.exists()


def test_cleanup_does_not_remove_replaced_owner_marker(tmp_path: Path) -> None:
    lock_path = tmp_path / ".sync.lock"
    replacement = {
        "run_id": "successor-run",
        "pid": 45678,
        "host": "other-host.invalid",
        "trigger": "scheduler",
    }

    with _sync_lock(lock_path, run_id="first-run", trigger="test"):
        lock_path.write_text(json.dumps(replacement), encoding="utf-8")

    assert json.loads(lock_path.read_text(encoding="utf-8")) == replacement
