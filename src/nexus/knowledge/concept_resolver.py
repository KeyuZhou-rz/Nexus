"""
Concept Resolver — Nexus Phase M1-3

三级概念解析：将用户输入的概念名/别名解析为规范 concept_id。

Level 1: concept_aliases 表精确匹配（alias + course_id）
Level 2: concepts 表规范化精确匹配（去空格、小写、去除标点后比对）
Level 3: 模糊匹配（difflib.SequenceMatcher ≥ 0.7）

Usage:
    resolver = ConceptResolver(store)
    result = resolver.resolve("死锁", "CS202_OS")
    if result:
        print(result.concept_id)  # "cs202_os__deadlock"
        print(result.level)       # 1, 2, or 3
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from .sqlite_store import SQLiteStore


FUZZY_THRESHOLD = 0.7


@dataclass
class ResolutionResult:
    """概念解析结果。"""
    concept_id: str
    name: str           # 规范显示名
    course_id: str
    level: int          # 1=别名精确, 2=规范化精确, 3=模糊匹配
    confidence: float   # 1.0 for level 1/2, ≥0.7 for level 3


def _normalize(text: str) -> str:
    """规范化文本：小写 + 去空格 + 去标点符号（保留中文字符）。"""
    text = text.strip().lower()
    # 移除 ASCII 标点和空格，保留字母、数字、中文
    text = re.sub(r"[^\w一-鿿]+", "", text, flags=re.UNICODE)
    return text


def _fuzzy_score(query: str, target: str) -> float:
    """返回两个字符串之间的模糊匹配分数。"""
    return difflib.SequenceMatcher(None, query, target).ratio()


class ConceptResolver:
    """
    三级概念解析器。

    参数:
    - store: SQLiteStore 实例
    - fuzzy_threshold: Level 3 模糊匹配最低分数线（默认 0.7）
    """

    def __init__(
        self,
        store: SQLiteStore,
        fuzzy_threshold: float = FUZZY_THRESHOLD,
    ) -> None:
        self._store = store
        self._threshold = fuzzy_threshold

    def resolve(
        self, query: str, course_id: str
    ) -> ResolutionResult | None:
        """
        三级解析：别名 → 规范化 → 模糊匹配。
        返回 None 表示无法解析。
        """
        query = query.strip()
        if not query:
            return None

        # Level 1: 别名表精确匹配
        result = self._resolve_alias(query, course_id)
        if result:
            return result

        # Level 2: 概念名规范化精确匹配
        result = self._resolve_normalized(query, course_id)
        if result:
            return result

        # Level 3: 模糊匹配
        result = self._resolve_fuzzy(query, course_id)
        if result:
            return result

        return None

    def resolve_bulk(
        self, queries: list[str], course_id: str
    ) -> list[ResolutionResult | None]:
        """
        批量解析多个概念名。
        结果顺序与输入一致。
        """
        return [self.resolve(q, course_id) for q in queries]

    def add_alias(
        self,
        alias: str,
        course_id: str,
        concept_id: str,
        source: str = "manual",
    ) -> None:
        """为概念添加别名（便捷方法）。"""
        self._store.upsert_alias(
            alias=alias.strip().lower(),
            course_id=course_id,
            concept_id=concept_id,
            source=source,
        )

    # ────────────────────────────────────────────────
    # 三级实现
    # ────────────────────────────────────────────────

    def _resolve_alias(
        self, query: str, course_id: str
    ) -> ResolutionResult | None:
        """Level 1: 别名表精确查找。"""
        row = self._store.get_alias(query, course_id)
        if row is None:
            return None

        concept = self._store.get_concept(row["concept_id"])
        if concept is None:
            return None  # 孤立别名（concept 被删除）

        return ResolutionResult(
            concept_id=concept["concept_id"],
            name=concept["name"],
            course_id=concept["course_id"],
            level=1,
            confidence=row.get("confidence", 1.0),
        )

    def _resolve_normalized(
        self, query: str, course_id: str
    ) -> ResolutionResult | None:
        """Level 2: 规范化后与 concepts 表精确比对。"""
        query_norm = _normalize(query)
        if not query_norm:
            return None

        concepts = self._store.get_concepts_by_course(course_id)

        for c in concepts:
            # 规范化概念名（可能包含中文）
            name_norm = _normalize(c["name"])
            if name_norm == query_norm:
                return ResolutionResult(
                    concept_id=c["concept_id"],
                    name=c["name"],
                    course_id=c["course_id"],
                    level=2,
                    confidence=1.0,
                )

        return None

    def _resolve_fuzzy(
        self, query: str, course_id: str
    ) -> ResolutionResult | None:
        """Level 3: difflib 模糊匹配，阈值 ≥ threshold。"""
        query_norm = _normalize(query)
        if not query_norm:
            return None

        concepts = self._store.get_concepts_by_course(course_id)

        best_score = 0.0
        best_match: dict[str, Any] | None = None

        for c in concepts:
            name_norm = _normalize(c["name"])
            score = _fuzzy_score(query_norm, name_norm)

            if score > best_score:
                best_score = score
                best_match = c

        if best_match and best_score >= self._threshold:
            return ResolutionResult(
                concept_id=best_match["concept_id"],
                name=best_match["name"],
                course_id=best_match["course_id"],
                level=3,
                confidence=round(best_score, 4),
            )

        return None
