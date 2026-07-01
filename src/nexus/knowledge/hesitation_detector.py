"""
Hesitation Detector — Nexus Phase N (Memory Layer 1)

轻量级正则引擎，检测用户查询中的犹豫/不确定信号。
零 LLM 成本，亚毫秒延迟，用于 concept_query_log 的 had_hesitation 标记。

用法:
    detector = HesitationDetector()
    detector.detect("我不太确定死锁的定义")  # → True
    detector.detect_detail("um, I'm not sure about this")
    # → {"has_hesitation": True, "matched_patterns": [... ]}
"""
from __future__ import annotations

import re
from typing import ClassVar


class HesitationDetector:
    """Stateless regex-based hesitation signal detector."""

    HESITATION_PATTERNS: ClassVar[list[str]] = [
        # Chinese hesitation
        r"不太确定|不太清楚|不确定|不清楚",
        r"可能吧|也许是|好像是|应该是|大概是",
        r"是吗|对吗|对不对|是吧",
        r"大概|也许|可能|好像|似乎",
        r"我记得|我忘了|想不起来|记不清",
        r"感觉|觉得|应该",
        # English hesitation
        r"\bi'?m\s+not\s+sure\b",
        r"\bi\s+don'?t\s+know\b",
        r"\bi\s+guess\b",
        r"\bmaybe\b|\bperhaps\b|\bprobably\b",
        r"\bi\s+think\s+so\b",
        r"\bis\s+that\s+right\b",
        r"\bdoes\s+that\s+make\s+sense\b",
        r"\bam\s+i\s+wrong\b",
        # Filled pauses
        r"\bum+\b|\buh+\b|\bhmm+\b|\ber+\b",
        # Multiple question marks
        r"\?{2,}",
    ]

    def __init__(self) -> None:
        self._compiled: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE) for p in self.HESITATION_PATTERNS
        ]

    def detect(self, text: str) -> bool:
        """Return True if any hesitation pattern matches the text."""
        if not text:
            return False
        return any(p.search(text) for p in self._compiled)

    def detect_detail(self, text: str) -> dict:
        """Return {has_hesitation: bool, matched_patterns: [str]} for logging."""
        matches: list[str] = []
        if text:
            for i, p in enumerate(self._compiled):
                if p.search(text):
                    matches.append(self.HESITATION_PATTERNS[i])
        return {
            "has_hesitation": bool(matches),
            "matched_patterns": matches,
        }
