"""Tests for QAPipeline._update_concept_mastery / _classify_mastery_events (Phase 4B fix)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.knowledge.qa_pipeline import QAPipeline
from nexus.knowledge.sqlite_store import SQLiteStore


@pytest.fixture
def tmp_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    store = SQLiteStore(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO chunks (chunk_id, document_id, course_id, topic, type)
               VALUES (?, ?, ?, ?, ?)""",
            ("CS202_OS_001", "CS202_OS/doc1", "CS202_OS", "Deadlock", "definition"),
        )
        conn.execute(
            "INSERT INTO concepts (concept_id, name, course_id) VALUES (?, ?, ?)",
            ("cs202_os__deadlock", "Deadlock", "CS202_OS"),
        )
        conn.execute(
            "INSERT INTO chunk_concepts (chunk_id, concept_id) VALUES (?, ?)",
            ("CS202_OS_001", "cs202_os__deadlock"),
        )

    yield db_path, store
    db_path.unlink(missing_ok=True)


def _make_pipeline(db_path: Path) -> QAPipeline:
    with tempfile.TemporaryDirectory() as chroma_dir:
        pass
    # Avoid Chroma init cost/side-effects: build pipeline with a throwaway chroma dir.
    chroma_dir = tempfile.mkdtemp()
    return QAPipeline(chroma_dir=Path(chroma_dir), sqlite_path=db_path)


class TestClassifyMasteryEvents:
    def test_llm_classification_used_when_available(self, tmp_store):
        db_path, store = tmp_store
        pipeline = _make_pipeline(db_path)

        fake_response = MagicMock()
        fake_response.content = json.dumps(
            {"events": [{"concept": "Deadlock", "type": "struggled"}]}
        )

        with patch("nexus.knowledge.llm_gateway.gateway.chat", return_value=fake_response):
            pipeline._update_concept_mastery(
                chunk_ids=["CS202_OS_001"],
                session_id="s1",
                user_query="我不太懂死锁的四个必要条件",
                response_summary="死锁需要互斥、持有等待、不可抢占、循环等待。",
            )

        row = store.get_mastery("cs202_os__deadlock")
        assert row is not None
        # struggled: mastery_add=0.05, decay_mult=1.10 (starting from 0.0/0.3 defaults)
        assert row["initial_mastery"] == pytest.approx(0.05)
        assert row["decay_rate"] == pytest.approx(0.33)

    def test_falls_back_to_reviewed_on_llm_failure(self, tmp_store):
        db_path, store = tmp_store
        pipeline = _make_pipeline(db_path)

        with patch(
            "nexus.knowledge.llm_gateway.gateway.chat", side_effect=RuntimeError("boom")
        ):
            pipeline._update_concept_mastery(
                chunk_ids=["CS202_OS_001"],
                session_id="s1",
                user_query="什么是死锁",
                response_summary="死锁是...",
            )

        row = store.get_mastery("cs202_os__deadlock")
        assert row is not None
        # reviewed: mastery_add=0.10, decay_mult=0.90 (starting from 0.0/0.3 defaults)
        assert row["initial_mastery"] == pytest.approx(0.10)
        assert row["decay_rate"] == pytest.approx(0.27)

    def test_invalid_event_type_falls_back_to_reviewed(self, tmp_store):
        db_path, store = tmp_store
        pipeline = _make_pipeline(db_path)

        fake_response = MagicMock()
        fake_response.content = json.dumps(
            {"events": [{"concept": "Deadlock", "type": "not_a_real_event"}]}
        )

        with patch("nexus.knowledge.llm_gateway.gateway.chat", return_value=fake_response):
            pipeline._update_concept_mastery(
                chunk_ids=["CS202_OS_001"],
                session_id="s1",
                user_query="死锁是什么",
                response_summary="...",
            )

        row = store.get_mastery("cs202_os__deadlock")
        assert row["initial_mastery"] == pytest.approx(0.10)
