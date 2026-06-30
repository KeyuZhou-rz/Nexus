"""Phase M1-3: ConceptResolver three-level resolution tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from nexus.knowledge.concept_resolver import (
    ConceptResolver,
    ResolutionResult,
    _fuzzy_score,
    _normalize,
)
from nexus.knowledge.sqlite_store import SQLiteStore


# ──────────────────────────────────────────
# _normalize helper
# ──────────────────────────────────────────


def test_normalize_lowercase():
    assert _normalize("DeadLock") == "deadlock"


def test_normalize_whitespace():
    assert _normalize("  dead lock  ") == "deadlock"


def test_normalize_punctuation():
    assert _normalize("dead-lock! (detection)") == "deadlockdetection"


def test_normalize_chinese_preserved():
    assert _normalize("死锁 / Deadlock") == "死锁deadlock"


def test_normalize_empty():
    assert _normalize("   ") == ""


# ──────────────────────────────────────────
# _fuzzy_score helper
# ──────────────────────────────────────────


def test_fuzzy_exact_match():
    assert _fuzzy_score("deadlock", "deadlock") == 1.0


def test_fuzzy_partial_match():
    score = _fuzzy_score("deadlock", "dead lock detection")
    assert 0.4 < score < 0.9  # partial match


def test_fuzzy_no_match():
    score = _fuzzy_score("deadlock", "scheduling")
    assert score < 0.5


# ──────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    """创建带测试概念的 SQLiteStore。"""
    db_path = tmp_path / "test.db"
    s = SQLiteStore(db_path)

    # 写入测试概念
    s.upsert_concept(
        concept_id="cs202_os__deadlock",
        name="死锁 / Deadlock",
        course_id="CS202_OS",
        definition="多个进程互相等待对方持有的资源",
        concept_type="fundamental",
    )
    s.upsert_concept(
        concept_id="cs202_os__deadlock_detection",
        name="死锁检测 / Deadlock Detection",
        course_id="CS202_OS",
        definition="通过资源分配图检测死锁",
        concept_type="technique",
    )
    s.upsert_concept(
        concept_id="cs202_os__scheduling",
        name="CPU Scheduling",
        course_id="CS202_OS",
        definition="操作系统调度算法",
        concept_type="fundamental",
    )
    # Another course for cross-course testing
    s.upsert_concept(
        concept_id="cs301_net__tcp",
        name="TCP",
        course_id="CS301_NET",
        definition="Transmission Control Protocol",
        concept_type="fundamental",
    )
    return s


@pytest.fixture
def resolver(store: SQLiteStore) -> ConceptResolver:
    """创建带预填别名的 ConceptResolver。"""
    r = ConceptResolver(store)
    # Level 1 aliases
    store.upsert_alias("dl", "CS202_OS", "cs202_os__deadlock", source="manual")
    store.upsert_alias("dead lock", "CS202_OS", "cs202_os__deadlock", source="memory")
    store.upsert_alias("调度", "CS202_OS", "cs202_os__scheduling", source="llm_extracted")
    return r


# ──────────────────────────────────────────
# Level 1: Alias exact match
# ──────────────────────────────────────────


def test_level1_alias_dl(resolver: ConceptResolver):
    result = resolver.resolve("dl", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock"
    assert result.level == 1
    assert result.confidence == 1.0


def test_level1_alias_dead_lock(resolver: ConceptResolver):
    result = resolver.resolve("dead lock", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock"
    assert result.level == 1


def test_level1_alias_scheduling(resolver: ConceptResolver):
    result = resolver.resolve("调度", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__scheduling"
    assert result.level == 1


def test_level1_alias_case_insensitive(resolver: ConceptResolver):
    """Alias lookup is case-insensitive (stored lowered)."""
    result = resolver.resolve("DL", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock"
    assert result.level == 1


def test_level1_alias_wrong_course(resolver: ConceptResolver):
    """Alias is scoped to course_id."""
    result = resolver.resolve("dl", "CS301_NET")
    assert result is None  # dl is only registered for CS202_OS


# ──────────────────────────────────────────
# Level 2: Normalized exact match
# ──────────────────────────────────────────


def test_level2_exact_name(resolver: ConceptResolver):
    result = resolver.resolve("死锁 / Deadlock", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock"
    assert result.level == 2
    assert result.confidence == 1.0


def test_level2_different_whitespace(resolver: ConceptResolver):
    result = resolver.resolve("  CPU   Scheduling  ", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__scheduling"
    assert result.level == 2


def test_level2_punctuation_variation(resolver: ConceptResolver):
    result = resolver.resolve("死锁-Deadlock!", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock"
    assert result.level == 2


def test_level2_partial_not_match(resolver: ConceptResolver):
    """Normalized must match exactly, not partially."""
    result = resolver.resolve("死锁", "CS202_OS")  # Just "死锁" != "死锁deadlock" after normalize
    assert result is None or result.level != 2


# ──────────────────────────────────────────
# Level 3: Fuzzy match
# ──────────────────────────────────────────


def test_level3_fuzzy_deadlock(resolver: ConceptResolver):
    """Slightly misspelled 'deadlok' should fuzzy match 'deadlock'."""
    # "deadlok" normalized → fuzzy against "死锁deadlockdetection" etc
    result = resolver.resolve("deadlok", "CS202_OS")
    assert result is not None
    assert result.level == 3
    assert result.confidence >= 0.7


def test_level3_fuzzy_scheduling(resolver: ConceptResolver):
    result = resolver.resolve("CPU schedule", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__scheduling"
    assert result.level == 3
    assert result.confidence >= 0.7


def test_level3_fuzzy_below_threshold(store: SQLiteStore):
    """Completely unrelated query should not match."""
    resolver = ConceptResolver(store, fuzzy_threshold=0.7)
    result = resolver.resolve("量子力学", "CS202_OS")
    assert result is None  # No way this matches anything in CS202_OS


def test_level3_fuzzy_prefers_highest_score(store: SQLiteStore):
    """When multiple fuzzy matches exist, return the highest score."""
    resolver = ConceptResolver(store)
    # "dead" should fuzzy match deadlock more than deadlock_detection
    result = resolver.resolve("dead", "CS202_OS")
    if result:
        assert result.level == 3
        # deadlock_detection has "deadlock" in its normalized name
        assert "deadlock" in result.concept_id


# ──────────────────────────────────────────
# No match / edge cases
# ──────────────────────────────────────────


def test_resolve_empty_query(resolver: ConceptResolver):
    result = resolver.resolve("", "CS202_OS")
    assert result is None


def test_resolve_whitespace_only(resolver: ConceptResolver):
    result = resolver.resolve("   ", "CS202_OS")
    assert result is None


def test_resolve_unknown_without_alias_or_concept(store: SQLiteStore):
    """Query not matching any alias, name, or fuzzy threshold."""
    resolver = ConceptResolver(store)
    result = resolver.resolve("completely_unknown_topic_xyz", "CS202_OS")
    assert result is None


def test_resolve_cross_course_no_leak(store: SQLiteStore):
    """Concepts from CS301_NET should not be returned for CS202_OS queries."""
    resolver = ConceptResolver(store)
    result = resolver.resolve("TCP", "CS202_OS")
    # TCP is only in CS301_NET, not CS202_OS
    assert result is None


# ──────────────────────────────────────────
# resolve_bulk
# ──────────────────────────────────────────


def test_resolve_bulk(resolver: ConceptResolver):
    results = resolver.resolve_bulk(["dl", "CPU scheduling", "unknown_xyz"], "CS202_OS")
    assert results[0] is not None
    assert results[0].concept_id == "cs202_os__deadlock"
    assert results[1] is not None
    assert results[1].concept_id == "cs202_os__scheduling"
    assert results[2] is None


# ──────────────────────────────────────────
# add_alias convenience
# ──────────────────────────────────────────


def test_add_alias_then_resolve(store: SQLiteStore):
    resolver = ConceptResolver(store)
    resolver.add_alias("deadlock detection", "CS202_OS", "cs202_os__deadlock_detection")
    result = resolver.resolve("deadlock detection", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock_detection"
    assert result.level == 1


def test_add_alias_overwrite(store: SQLiteStore):
    """Re-adding same alias updates the mapping."""
    resolver = ConceptResolver(store)
    resolver.add_alias("dl", "CS202_OS", "cs202_os__deadlock_detection")
    result = resolver.resolve("dl", "CS202_OS")
    assert result is not None
    assert result.concept_id == "cs202_os__deadlock_detection"


# ──────────────────────────────────────────
# ResolutionResult
# ──────────────────────────────────────────


def test_resolution_result_dataclass():
    r = ResolutionResult(
        concept_id="cs202_os__x",
        name="X",
        course_id="CS202_OS",
        level=1,
        confidence=1.0,
    )
    assert r.concept_id == "cs202_os__x"
    assert r.level == 1
