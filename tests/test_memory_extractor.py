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
