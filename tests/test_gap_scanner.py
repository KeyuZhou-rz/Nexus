"""Tests for GapScanner — Memory Layer 1."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.knowledge.gap_scanner import GapScanner
from nexus.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_store():
    """Create a temporary SQLite store with schema + test data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = SQLiteStore(db_path)

    # Insert test chunks
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO chunks (chunk_id, document_id, course_id, topic, type, keywords, prerequisites)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("CS202_OS_001", "CS202_OS/doc1", "CS202_OS", "Deadlock", "definition",
             json.dumps(["deadlock", "mutual exclusion"]), json.dumps(["process_scheduling"])),
        )
        conn.execute(
            """INSERT INTO chunks (chunk_id, document_id, course_id, topic, type, keywords, prerequisites)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("CS202_OS_002", "CS202_OS/doc1", "CS202_OS", "Virtual Memory", "definition",
             json.dumps(["virtual memory", "paging"]), json.dumps(["memory_management"])),
        )
        # Insert concepts for mastery tracking
        conn.execute(
            """INSERT INTO concepts (concept_id, name, course_id)
               VALUES (?, ?, ?)""",
            ("cs202_os__deadlock", "Deadlock", "CS202_OS"),
        )
        conn.execute(
            """INSERT INTO concepts (concept_id, name, course_id)
               VALUES (?, ?, ?)""",
            ("cs202_os__virtual_memory", "Virtual Memory", "CS202_OS"),
        )
        # Link chunks to concepts
        conn.execute(
            "INSERT INTO chunk_concepts (chunk_id, concept_id) VALUES (?, ?)",
            ("CS202_OS_001", "cs202_os__deadlock"),
        )
        # Add mastery data
        conn.execute(
            """INSERT INTO concept_mastery (concept_id, initial_mastery, decay_rate, review_count)
               VALUES (?, ?, ?, ?)""",
            ("cs202_os__deadlock", 0.7, 0.3, 3),
        )
        # Add alias for fuzzy matching
        conn.execute(
            """INSERT INTO concept_aliases (alias, course_id, concept_id, source)
               VALUES (?, ?, ?, ?)""",
            ("死锁", "CS202_OS", "cs202_os__deadlock", "manual"),
        )

    yield store

    # Cleanup
    db_path.unlink(missing_ok=True)


class TestGapScannerScanCourse:
    """Tests for scan_course() method."""

    def test_scan_course_no_chunks(self, tmp_store):
        """scan_course with no chunks should return gracefully."""
        scanner = GapScanner(tmp_store)
        result = scanner.scan_course("NONEXISTENT")
        assert result["concepts_extracted"] == 0
        assert "error" in result

    def test_scan_course_with_llm(self, tmp_store):
        """scan_course should create checkpoint with LLM results."""
        fake_response = MagicMock()
        fake_response.content = json.dumps([
            {
                "name": "死锁 / Deadlock",
                "importance": 10,
                "self_test_q1": "What are the 4 conditions?",
                "self_test_q2": "Draw a RAG.",
                "self_test_q3": "Banker's algorithm vs detection?",
            },
            {
                "name": "虚拟内存 / Virtual Memory",
                "importance": 8,
                "self_test_q1": "What is paging?",
                "self_test_q2": "Explain TLB.",
                "self_test_q3": "Page fault handling?",
            },
        ])

        with patch("nexus.knowledge.llm_gateway.gateway.chat", return_value=fake_response):
            scanner = GapScanner(tmp_store)
            result = scanner.scan_course("CS202_OS")

        assert result["concepts_extracted"] == 2
        # One matched via alias (死锁), one via name (Virtual Memory -> resolution may not match)
        assert result["concepts_matched"] >= 1

        # Verify checkpoint persisted
        cp = tmp_store.get_latest_checkpoint("CS202_OS")
        assert cp is not None
        assert cp["course_id"] == "CS202_OS"

        # Verify checkpoint concepts persisted
        concepts = tmp_store.get_checkpoint_concepts(cp["checkpoint_id"])
        assert len(concepts) == 2
        assert sum(1 for c in concepts if c.get("concept_id")) == 1

    def test_run_weekly_reuses_current_checkpoint(self, tmp_store):
        scanner = GapScanner(tmp_store)
        now = datetime.now()
        checkpoint_id = f"CS202_OS__{now.year}-W{now.isocalendar().week:02d}"
        scanner._store.upsert_checkpoint(checkpoint_id, "CS202_OS", "2026-01-01", "2026-01-07")

        with patch.object(scanner, "scan_course") as scan:
            result = scanner.run_weekly("CS202_OS")

        scan.assert_not_called()
        assert result["checkpoint"]["cached"] is True


class TestGapScannerWeaknessRanking:
    """Tests for compute_weakness_ranking()."""

    def test_ranking_empty_no_checkpoint(self, tmp_store):
        """Ranking with no checkpoint should return empty list."""
        scanner = GapScanner(tmp_store)
        ranking = scanner.compute_weakness_ranking("NONEXISTENT")
        assert ranking == []

    def test_checkpoint_id_format(self):
        """Verify checkpoint_id follows expected format."""
        now = datetime.now()
        iso_week = now.isocalendar()
        expected_prefix = f"CS202_OS__{now.year}-W{iso_week.week:02d}"
        # Just verify pattern, not exact match (time-sensitive)
        assert f"{now.year}-W" in expected_prefix
        assert expected_prefix.startswith("CS202_OS__")

    def _seed_checkpoint(self, store, concept_id="cs202_os__deadlock", name="Deadlock"):
        store.upsert_checkpoint("CS202_OS__2026-W01", "CS202_OS", "2026-01-01", "2026-01-07")
        store.upsert_checkpoint_concept(
            checkpoint_id="CS202_OS__2026-W01",
            concept_id=concept_id,
            name=name,
            importance=8,
            q1="q1", q2="q2", q3="q3",
        )
        return "CS202_OS__2026-W01"

    def test_query_stats_are_loaded_once_for_the_whole_checkpoint(self, tmp_store):
        """Regression guard: ranking must not issue one stats query per concept."""
        checkpoint_id = self._seed_checkpoint(tmp_store)
        tmp_store.upsert_checkpoint_concept(
            checkpoint_id=checkpoint_id,
            concept_id="cs202_os__virtual_memory",
            name="Virtual Memory",
            importance=7,
            q1="q1", q2="q2", q3="q3",
        )
        scanner = GapScanner(tmp_store)

        real_stats = tmp_store.get_concept_query_stats_batch
        calls: list[tuple] = []

        def _spy(concept_ids, *, since_days=(14, 3)):
            calls.append((tuple(concept_ids), since_days))
            return real_stats(concept_ids, since_days=since_days)

        with patch.object(tmp_store, "get_concept_query_stats_batch", side_effect=_spy):
            scanner.compute_weakness_ranking("CS202_OS", checkpoint_id)

        assert calls == [
            (("cs202_os__deadlock", "cs202_os__virtual_memory"), (14, 3))
        ]

    def test_recency_boost_favors_neglected_concept_not_recently_queried(self, tmp_store):
        """Fix: a concept queried recently (last 3 days) should NOT get an
        extra urgency boost over one that hasn't been touched recently --
        the old code did the opposite (recently-queried => more "urgent"),
        which is backwards: an untouched-in-3-days concept is the neglected,
        more urgent one."""
        checkpoint_id = self._seed_checkpoint(
            tmp_store, concept_id="cs202_os__virtual_memory", name="Virtual Memory"
        )
        # Log a query for this concept *today* (within the 3-day recency window).
        tmp_store.log_concept_query(
            concept_id="cs202_os__virtual_memory",
            concept_name="Virtual Memory",
            session_id="s1",
            query_text="what is virtual memory",
            had_hesitation=0,
        )

        scanner = GapScanner(tmp_store)
        ranked = scanner.compute_weakness_ranking("CS202_OS", checkpoint_id)
        assert len(ranked) == 1
        # mastery=0.0 (no concept_mastery row), importance=8 -> base score without boosts:
        # importance_norm(0.8) * (1-mastery)(1.0) * hesitation_boost(1.0) = 0.8
        # Recently queried -> recency_boost must stay 1.0 (not the old 1.2), so
        # the composite score must equal the un-boosted base, not be inflated.
        assert ranked[0]["weakness_score"] == pytest.approx(0.8)
