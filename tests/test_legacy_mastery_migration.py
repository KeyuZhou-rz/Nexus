from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.cli.mastery_migrate import main as migration_main
from nexus.knowledge.maintenance import migrate_legacy_mastery
from nexus.knowledge.sqlite_store import SQLiteStore, connect
from nexus.knowledge.temporal_memory import TemporalMemory


def _legacy_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_concept("os__process", "Process", "OS")
    with connect(store.db_path) as conn:
        conn.executemany(
            """INSERT INTO learning_events
                   (concept_id, event_type, context, timestamp)
               VALUES ('os__process', ?, ?, ?)""",
            [
                ("reviewed", None, "2026-01-01 10:00:00"),
                ("correct_answer", None, "2026-01-02 10:00:00"),
                (
                    "correct_answer",
                    json.dumps({"evidence_type": "assessment", "confidence": 0.9}),
                    "2026-01-03 10:00:00",
                ),
            ],
        )
    store.upsert_mastery("os__process", 0.35, 0.20, review_count=3, streak=2)
    return store


def test_migration_dry_run_does_not_classify_or_rebuild(tmp_path: Path) -> None:
    store = _legacy_store(tmp_path)
    before = store.get_mastery("os__process")

    report = migrate_legacy_mastery(store.db_path)

    assert report["dry_run"]
    assert not report["applied"]
    assert report["candidates"] == 3
    assert report["will_mark_trusted"] == 1
    assert report["will_mark_legacy"] == 2
    assert report["will_mark_exposure"] == 1
    assert "db-backup" in report["backup_hint"]
    assert store.get_learning_event_stats()["unclassified"] == 3
    assert store.get_mastery("os__process") == before


def test_apply_preserves_raw_events_and_rebuilds_from_trusted_only(tmp_path: Path) -> None:
    store = _legacy_store(tmp_path)
    with connect(store.db_path) as conn:
        raw_before = [
            tuple(row)
            for row in conn.execute(
                "SELECT id, event_type, context, timestamp FROM learning_events ORDER BY id"
            )
        ]

    report = migrate_legacy_mastery(store.db_path, apply=True)

    assert report["applied"]
    stats = store.get_learning_event_stats()
    assert stats == {
        "total": 3,
        "trusted": 1,
        "legacy": 2,
        "unclassified": 0,
        "by_evidence_type": {"assessment": 1, "exposure": 1, "legacy": 1},
    }
    events = store.get_learning_events("os__process")
    assert [event["event_type"] for event in events] == [
        "reviewed", "correct_answer", "correct_answer"
    ]
    assert events[0]["evidence_type"] == "exposure"
    assert events[0]["trust_status"] == "legacy"
    assert [event["id"] for event in store.get_learning_events("os__process", trusted_only=True)] == [events[2]["id"]]
    with connect(store.db_path) as conn:
        raw_after = [
            tuple(row)
            for row in conn.execute(
                "SELECT id, event_type, context, timestamp FROM learning_events ORDER BY id"
            )
        ]
    assert raw_after == raw_before
    mastery = store.get_mastery("os__process")
    assert mastery["initial_mastery"] == pytest.approx(0.15)
    assert mastery["decay_rate"] == pytest.approx(0.255)
    assert mastery["review_count"] == 1
    assert mastery["streak"] == 1
    assert mastery["last_reviewed"] == "2026-01-03 10:00:00"


def test_apply_is_idempotent(tmp_path: Path) -> None:
    store = _legacy_store(tmp_path)
    migrate_legacy_mastery(store.db_path, apply=True)
    mastery_before = store.get_mastery("os__process")
    classified_before = [event["classified_at"] for event in store.get_learning_events("os__process")]

    second = migrate_legacy_mastery(store.db_path, apply=True)

    assert second["candidates"] == 0
    assert not second["applied"]
    assert store.get_mastery("os__process") == mastery_before
    assert [event["classified_at"] for event in store.get_learning_events("os__process")] == classified_before


def test_new_reviewed_event_is_exposure_and_does_not_change_mastery(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "nexus.db")
    store.upsert_concept("os__process", "Process", "OS")
    memory = TemporalMemory(store)

    assert memory.record_event("os__process", "reviewed") is None
    assert store.get_mastery("os__process") is None
    event = store.get_learning_events("os__process")[0]
    assert event["event_type"] == "reviewed"
    assert event["evidence_type"] == "exposure"
    assert event["trust_status"] == "legacy"


def test_cli_defaults_to_dry_run(tmp_path: Path, capsys) -> None:
    store = _legacy_store(tmp_path)

    assert migration_main(["--sqlite-path", str(store.db_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"]
    assert store.get_learning_event_stats()["unclassified"] == 3
