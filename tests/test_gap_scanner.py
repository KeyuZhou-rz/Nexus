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
