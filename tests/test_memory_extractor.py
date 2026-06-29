from __future__ import annotations

from nexus.memory_extractor import extract_weak_points


def test_extract_weak_points_english_pattern():
    messages = [
        {
            "id": "m1",
            "role": "user",
            "content": "I don't understand Laplace transform in circuits.",
        }
    ]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert "laplace transform" in cands[0].topic


def test_extract_weak_points_zh_pattern():
    messages = [{"id": "m1", "role": "user", "content": "我不懂基尔霍夫电流定律"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert "基尔霍夫" in cands[0].topic


# ---------- T2 supplemental tests ----------


def test_extract_empty_messages():
    """Empty input list returns empty list."""
    assert extract_weak_points([]) == []


def test_extract_non_user_role_skipped():
    """Messages from assistant (even with weak-point words) are skipped."""
    messages = [{"id": "m1", "role": "assistant", "content": "I don't understand pointers"}]
    assert extract_weak_points(messages) == []


def test_extract_missing_content_key():
    """Message without 'content' key is treated as empty string and skipped."""
    messages = [{"id": "m1", "role": "user"}]
    assert extract_weak_points(messages) == []


def test_extract_no_weak_signal():
    """User message with no weak-point pattern returns empty list."""
    messages = [{"id": "m1", "role": "user", "content": "I love studying operating systems."}]
    assert extract_weak_points(messages) == []


def test_extract_english_do_not_understand():
    """English 'do not understand X' yields correct topic and base confidence 0.7."""
    messages = [{"id": "m1", "role": "user", "content": "I do not understand virtual memory at all."}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert "virtual memory" in cands[0].topic
    assert cands[0].confidence == 0.7


def test_extract_english_confused_struggling_stuck():
    """Each of confused/struggling/stuck patterns extracts correct topic."""
    messages = [
        {"id": "m1", "role": "user", "content": "I am confused with pointers"},
        {"id": "m2", "role": "user", "content": "I am struggling with deadlocks"},
        {"id": "m3", "role": "user", "content": "I am stuck on semaphores"},
    ]
    cands = extract_weak_points(messages)
    assert len(cands) == 3
    topics = {c.topic for c in cands}
    assert "pointers" in topics
    assert "deadlocks" in topics
    assert "semaphores" in topics


def test_extract_chinese_pattern():
    """Chinese pattern '不会 X' extracts correct topic."""
    messages = [{"id": "m1", "role": "user", "content": "我不会进程调度"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert "进程调度" in cands[0].topic


def test_extract_high_intensity_confidence():
    """High-intensity word 'very' raises confidence to 0.8."""
    messages = [{"id": "m1", "role": "user", "content": "I am very confused with memory allocation"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert cands[0].confidence == 0.8


def test_extract_low_intensity_confidence():
    """Low-intensity phrase 'a bit' lowers confidence to 0.6."""
    messages = [{"id": "m1", "role": "user", "content": "I am a bit confused with memory allocation"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert cands[0].confidence == 0.6


def test_extract_high_low_coexist_high_wins():
    """When both high- and low-intensity words appear, high wins (0.8)."""
    messages = [{"id": "m1", "role": "user", "content": "I am very confused with memory allocation, maybe a bit stuck on it too"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert cands[0].confidence == 0.8


def test_extract_two_patterns_one_message():
    """One message hitting two different patterns yields two candidates, both with same msg id."""
    messages = [
        {"id": "m1", "role": "user", "content": "I don't understand pointers. I am confused with recursion."}
    ]
    cands = extract_weak_points(messages)
    assert len(cands) == 2
    topics = {c.topic for c in cands}
    assert "pointers" in topics
    assert "recursion" in topics
    for c in cands:
        assert c.evidence_msg_ids == ["m1"]


def test_extract_merge_same_topic():
    """Two messages with same topic merge: evidence from first, msg_ids combined, conf blended."""
    messages = [
        {"id": "m1", "role": "user", "content": "I don't understand recursion"},
        {"id": "m2", "role": "user", "content": "I am confused with recursion"},
    ]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    c = cands[0]
    assert c.evidence_msg_ids == ["m1", "m2"]
    # Both base 0.7: min(0.95, 0.7*0.6 + 0.7*0.4) = 0.7
    assert c.confidence == 0.7
    # evidence from first message
    assert c.evidence == "I don't understand recursion"


def test_extract_merge_different_confidence():
    """Merge with different confidences: high-intensity first then base."""
    messages = [
        {"id": "m1", "role": "user", "content": "I am very confused with recursion"},
        {"id": "m2", "role": "user", "content": "I am confused with recursion"},
    ]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    # m1: conf=0.8 (very), m2: conf=0.7 (base)
    # merge: min(0.95, 0.8*0.6 + 0.7*0.4) = min(0.95, 0.48 + 0.28) = 0.76
    assert cands[0].confidence == 0.76


def test_extract_evidence_truncation():
    """Evidence is truncated to 220 characters from a long content."""
    padding = "x" * 250
    content = f"I don't understand {padding} end"
    messages = [{"id": "m1", "role": "user", "content": content}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert len(cands[0].evidence) == 220
    assert cands[0].evidence == content[:220]


def test_extract_msg_id_missing():
    """Message without 'id' key yields evidence_msg_ids == []."""
    messages = [{"role": "user", "content": "I don't understand recursion"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert cands[0].evidence_msg_ids == []


def test_extract_msg_id_empty_string():
    """Message with id='' yields evidence_msg_ids == []."""
    messages = [{"id": "", "role": "user", "content": "I don't understand recursion"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert cands[0].evidence_msg_ids == []


def test_extract_topic_normalization_strips_parens():
    """Non-CJK/alpha/num/dash/space chars (like parens) are stripped from topic."""
    messages = [
        {"id": "m1", "role": "user", "content": "I don't understand Laplace Transform (in circuits)"}
    ]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    # Parens stripped, whitespace collapsed, lowercased
    assert cands[0].topic == "laplace transform in circuits"


def test_extract_topic_too_short_dropped():
    """Regex matches but after normalization topic len<2, so guard drops it (not regex mismatch)."""
    messages = [{"id": "m1", "role": "user", "content": "I am confused with ,a,"}]
    cands = extract_weak_points(messages)
    assert cands == []


def test_extract_role_case_insensitive():
    """Role='User' (capitalized) is still treated as user and matched."""
    messages = [{"id": "m1", "role": "User", "content": "I don't understand recursion"}]
    cands = extract_weak_points(messages)
    assert len(cands) == 1
    assert "recursion" in cands[0].topic
