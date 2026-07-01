"""Tests for HesitationDetector — Memory Layer 1."""
from __future__ import annotations

import pytest
from nexus.knowledge.hesitation_detector import HesitationDetector


@pytest.fixture
def detector() -> HesitationDetector:
    return HesitationDetector()


def test_no_hesitation_clear_question(detector):
    """Clear, confident question should return False."""
    assert detector.detect("What is deadlock and how to prevent it?") is False
    assert detector.detect("死锁的定义是什么？请解释。") is False


def test_chinese_hesitation(detector):
    """Chinese hesitation markers should return True."""
    assert detector.detect("我不太确定死锁的定义") is True
    assert detector.detect("可能是银行家算法的问题") is True
    assert detector.detect("好像是虚拟内存和物理内存的区别") is True
    assert detector.detect("应该是四个必要条件吧") is True


def test_english_hesitation(detector):
    """English hesitation markers should return True."""
    assert detector.detect("I'm not sure about the definition of deadlock") is True
    assert detector.detect("I don't know if that's correct") is True
    assert detector.detect("I guess it might be related to synchronization") is True
    assert detector.detect("Maybe it has something to do with mutual exclusion") is True


def test_multiple_question_marks(detector):
    """Multiple question marks signal uncertainty."""
    assert detector.detect("what is deadlock??") is True
    assert detector.detect("is it really like that???") is True
    assert detector.detect("What is deadlock?") is False  # single question mark is fine


def test_detect_detail_returns_matched_patterns(detector):
    """detect_detail should return has_hesitation and matched_patterns."""
    result = detector.detect_detail("我不太确定这是否正确")
    assert result["has_hesitation"] is True
    assert len(result["matched_patterns"]) > 0

    result_clear = detector.detect_detail("请解释死锁的四个条件")
    assert result_clear["has_hesitation"] is False
    assert result_clear["matched_patterns"] == []
