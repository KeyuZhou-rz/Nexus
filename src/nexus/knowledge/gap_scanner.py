"""
Gap Scanner — Nexus Phase N (Memory Layer 1)

每周自动扫描课程所有 Chunk，调用 LLM 提取 Top 20 核心概念 + 自测题，
结合 TemporalMemory 掌握度 + concept_query_log 犹豫信号，计算动态薄弱点排名。

用法:
    store = SQLiteStore(Path("data/nexus.db"))
    scanner = GapScanner(store)
    result = scanner.run_weekly("CS202_OS")
    # result["weakness_ranking"] → 按 weakness_score 降序排列的概念列表
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from .concept_resolver import ConceptResolver
from .prompts import GAP_SCAN_PROMPT
from .sqlite_store import SQLiteStore
from .temporal_memory import TemporalMemory

logger = logging.getLogger(__name__)


class GapScanner:
    """知识漏洞扫描器。"""

    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._store = sqlite_store
        self._temporal = TemporalMemory(sqlite_store)
        self._resolver = ConceptResolver(sqlite_store)

    # ── Weekly Scan ──────────────────────────────────────────────

    def scan_course(self, course_id: str) -> dict[str, Any]:
        """
        LLM 扫描课程所有 chunk，提取 Top 20 核心概念，
        持久化到 concept_checkpoints + checkpoint_concepts 表。

        返回: {checkpoint_id, concepts_extracted, concepts_matched}
        """
        now = datetime.now()
        iso_week = now.isocalendar()
        checkpoint_id = f"{course_id}__{now.year}-W{iso_week.week:02d}"
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        week_end = (now + timedelta(days=6 - now.weekday())).strftime("%Y-%m-%d")

        chunks = self._store.get_chunks_by_course(course_id)
        if not chunks:
            logger.warning(f"GapScanner: course {course_id} has no chunks")
            return {
                "checkpoint_id": checkpoint_id,
                "concepts_extracted": 0,
                "concepts_matched": 0,
                "error": "No chunks found for this course",
            }

        # Build chunk summaries for LLM
        summaries: list[str] = []
        for i, ch in enumerate(chunks):
            topic = ch.get("topic", "")
            chunk_type = ch.get("type", "")
            keywords_raw = ch.get("keywords", "[]")
            try:
                keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw
            except (json.JSONDecodeError, TypeError):
                keywords = []
            kw_str = ", ".join(keywords[:5]) if keywords else ""
            summaries.append(
                f"[{i}] {topic} (type={chunk_type})"
                + (f" keywords: {kw_str}" if kw_str else "")
            )

        chunk_summaries = "\n".join(summaries[:80])  # Cap at 80 chunks
        week_label = f"{now.year} Week {iso_week.week}"

        # LLM call via gateway
        concepts_extracted = 0
        concepts_matched = 0
        try:
            from .llm_gateway import gateway
            prompt = GAP_SCAN_PROMPT.format(
                course_id=course_id,
                week_label=week_label,
                chunk_summaries=chunk_summaries,
            )
            messages = [
                {"role": "system", "content": "You are a curriculum analyst. Return JSON only."},
                {"role": "user", "content": prompt},
            ]
            output = gateway.chat(messages, temperature=0.3)
            llm_concepts = json.loads(output.content)
        except Exception as exc:
            logger.warning(f"GapScanner LLM call failed: {exc}")
            llm_concepts = []

        if not isinstance(llm_concepts, list) or not llm_concepts:
            logger.warning("GapScanner: LLM returned empty or invalid concept list")
            self._store.upsert_checkpoint(checkpoint_id, course_id, week_start, week_end)
            return {
                "checkpoint_id": checkpoint_id,
                "concepts_extracted": 0,
                "concepts_matched": 0,
                "error": "LLM returned no concepts",
            }

        # Persist checkpoint
        self._store.upsert_checkpoint(checkpoint_id, course_id, week_start, week_end)

        for c in llm_concepts[:20]:
            name = c.get("name", "")
            if not name:
                continue
            importance = int(c.get("importance", 5))
            q1 = c.get("self_test_q1", "")
            q2 = c.get("self_test_q2", "")
            q3 = c.get("self_test_q3", "")

            # Try to match to existing concept
            resolved = self._resolver.resolve(name, course_id)
            matched_id = resolved.concept_id if resolved else None
            if matched_id and self._store.get_chunks_for_concept(matched_id):
                concepts_matched += 1
            else:
                matched_id = None

            self._store.upsert_checkpoint_concept(
                checkpoint_id=checkpoint_id,
                concept_id=matched_id,
                name=name,
                importance=importance,
                q1=q1, q2=q2, q3=q3,
            )
            concepts_extracted += 1

        logger.info(
            f"GapScanner: {checkpoint_id} → {concepts_extracted} concepts "
            f"({concepts_matched} matched to existing)"
        )
        return {
            "checkpoint_id": checkpoint_id,
            "concepts_extracted": concepts_extracted,
            "concepts_matched": concepts_matched,
        }

    # ── Weakness Ranking ─────────────────────────────────────────

    def compute_weakness_ranking(
        self,
        course_id: str,
        checkpoint_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        计算动态薄弱点排名。

        公式: score = importance_norm × (1 - mastery) × hesitation_boost × recency_boost

        返回: 按 weakness_score 降序排列的列表。
        """
        # Load checkpoint
        if checkpoint_id:
            cp = self._store.get_checkpoint_concepts(checkpoint_id)
        else:
            latest = self._store.get_latest_checkpoint(course_id)
            if not latest:
                return []
            checkpoint_id = latest["checkpoint_id"]
            cp = self._store.get_checkpoint_concepts(checkpoint_id)

        if not cp:
            return []

        ranked: list[dict[str, Any]] = []
        for c in cp:
            name = c["name"]
            cid = c.get("concept_id")
            importance = max(1, c.get("importance", 5))

            # Mastery (0-1)
            if cid:
                mastery = self._temporal.get_mastery(cid)
            else:
                mastery = 0.0

            # 每个 concept 最多查询 2 次 query stats（14 天窗口 + 3 天窗口），
            # 复用结果算 hesitation_boost / recency_boost / 输出字段，
            # 避免同一个 (cid, since_days) 重复查 3-4 次冗余 SQL。
            stats_14 = (
                self._store.get_concept_query_stats(cid, since_days=14)
                if cid else {"total_queries": 0, "hesitation_count": 0}
            )
            stats_3 = (
                self._store.get_concept_query_stats(cid, since_days=3)
                if cid else {"total_queries": 0, "hesitation_count": 0}
            )

            # Hesitation boost
            hesitation_boost = 1.0
            total_q = stats_14["total_queries"]
            hes_q = stats_14["hesitation_count"]
            if total_q > 0:
                hes_rate = hes_q / total_q
                hesitation_boost = 1.0 + 0.3 * hes_rate

            # Recency boost：近期完全没被查询过的概念（被忽视）才更紧急，
            # 而不是查得越勤越紧急（原逻辑语义反直觉）。
            recency_boost = 1.0
            if cid and stats_3["total_queries"] == 0:
                recency_boost = 1.2

            # Composite score
            importance_norm = importance / 10.0  # normalize to 0.1-1.0
            weakness_score = importance_norm * (1.0 - mastery) * hesitation_boost * recency_boost

            ranked.append({
                "name": name,
                "concept_id": cid,
                "importance": importance,
                "mastery": round(mastery, 3),
                "weakness_score": round(weakness_score, 3),
                "hesitation_count": stats_14["hesitation_count"],
                "total_queries": stats_14["total_queries"],
            })

        ranked.sort(key=lambda x: -x["weakness_score"])
        return ranked

    # ── Convenience ──────────────────────────────────────────────

    def run_weekly(self, course_id: str, *, force: bool = False) -> dict[str, Any]:
        """Reuse the current week's persisted checkpoint unless forced."""
        now = datetime.now()
        expected_id = f"{course_id}__{now.year}-W{now.isocalendar().week:02d}"
        latest = self._store.get_latest_checkpoint(course_id)
        if not force and latest and latest.get("checkpoint_id") == expected_id:
            concepts = self._store.get_checkpoint_concepts(expected_id)
            scan_result = {
                "checkpoint_id": expected_id,
                "concepts_extracted": len(concepts),
                "concepts_matched": sum(1 for c in concepts if c.get("concept_id")),
                "cached": True,
            }
        else:
            scan_result = self.scan_course(course_id)
            scan_result["cached"] = False
        ranking = self.compute_weakness_ranking(course_id, scan_result["checkpoint_id"])
        return {
            "checkpoint": scan_result,
            "weakness_ranking": ranking,
        }
