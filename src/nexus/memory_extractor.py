from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class WeakPointCandidate:
    topic: str
    confidence: float
    evidence: str
    evidence_msg_ids: list[str]


_WEAK_PATTERNS = [
    re.compile(r"\b(I|i)\s+(don'?t|do not)\s+understand\s+(?P<topic>[^.?!;]{3,80})"),
    re.compile(r"\b(confused|struggling|stuck)\s+(with|on)\s+(?P<topic>[^.?!;]{3,80})", re.IGNORECASE),
    re.compile(r"(不会|不懂|不理解|搞不清|薄弱)\s*(?P<topic>[\u4e00-\u9fffA-Za-z0-9\-\s]{2,40})"),
]

_TOPIC_CLEAN = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9\-\s]")


def _normalize_topic(raw: str) -> str:
    text = _TOPIC_CLEAN.sub(" ", raw or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_weak_points(messages: list[dict[str, Any]]) -> list[WeakPointCandidate]:
    candidates: dict[str, WeakPointCandidate] = {}

    for msg in messages:
        role = str(msg.get("role", "")).strip().lower()
        if role != "user":
            continue
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        msg_id = str(msg.get("id", ""))

        for pattern in _WEAK_PATTERNS:
            m = pattern.search(content)
            if not m:
                continue
            topic_raw = m.groupdict().get("topic") or ""
            topic = _normalize_topic(topic_raw)
            if len(topic) < 2:
                continue

            confidence = 0.7
            if any(word in content.lower() for word in ["very", "totally", "完全", "特别"]):
                confidence = 0.8
            elif any(word in content.lower() for word in ["a bit", "一点", "maybe"]):
                confidence = 0.6

            evidence = content[:220]
            if topic in candidates:
                prev = candidates[topic]
                merged_conf = min(0.95, (prev.confidence * 0.6) + (confidence * 0.4))
                msg_ids = list(dict.fromkeys([*prev.evidence_msg_ids, msg_id])) if msg_id else prev.evidence_msg_ids
                candidates[topic] = WeakPointCandidate(
                    topic=topic,
                    confidence=merged_conf,
                    evidence=prev.evidence,
                    evidence_msg_ids=msg_ids,
                )
            else:
                candidates[topic] = WeakPointCandidate(
                    topic=topic,
                    confidence=confidence,
                    evidence=evidence,
                    evidence_msg_ids=[msg_id] if msg_id else [],
                )

    return list(candidates.values())
