"""
Exam Retrospective — Nexus Phase N (Memory Layer 3)

考后复盘报告生成器。用户输入考试回忆 → LLM 分析：
- 实际考到的薄弱概念（actual_hits）
- 未预料的丢分盲点（blind_spots）
- 以为掌握但失败的概念（overestimated）
- 叙事性分析 + 记忆锚点

价值锚点：报告中包含"考前预测命中率"，证明系统的预判能力。
这成为用户下学期续费的核心理由。

用法:
    store = SQLiteStore(Path("data/nexus.db"))
    retro = ExamRetrospective(store)
    exam_id = retro.record_exam("CS202_OS", "2026-06-30", user_input="Mutex那题很难...")
    result = retro.generate_retrospective(exam_id, user_input)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .prompts import RETROSPECTIVE_PROMPT
from .sqlite_store import SQLiteStore
from .temporal_memory import TemporalMemory

logger = logging.getLogger(__name__)


class ExamRetrospective:
    """考后复盘报告生成器。"""

    def __init__(self, sqlite_store: SQLiteStore) -> None:
        self._store = sqlite_store
        self._temporal = TemporalMemory(sqlite_store)

    # ── Record Exam ──────────────────────────────────────────────

    def record_exam(
        self,
        course_id: str,
        exam_date: str,
        chapter_weights: dict[str, float] | None = None,
        user_input: str | None = None,
    ) -> str:
        """
        记录一次考试，返回 exam_id。

        exam_id 格式: "{course_id}__{exam_date}"
        """
        exam_id = f"{course_id}__{exam_date}"
        ch_json = json.dumps(chapter_weights, ensure_ascii=False) if chapter_weights else None
        self._store.upsert_exam_record(
            exam_id=exam_id,
            course_id=course_id,
            exam_date=exam_date,
            chapter_weights=ch_json,
            user_input=user_input,
        )
        logger.info(f"Exam recorded: {exam_id}")
        return exam_id

    # ── Generate Retrospective ───────────────────────────────────

    def generate_retrospective(
        self,
        exam_id: str,
        user_input: str,
    ) -> dict[str, Any]:
        """
        生成考后复盘报告。

        参数:
        - exam_id: 考试记录 ID
        - user_input: 学生的考后反馈（自由文字）

        返回包含 analysis_text、retention_hook 等字段的复盘报告。
        """
        # Load exam record
        exam = self._store.get_exam_record(exam_id)
        if not exam:
            return {"error": f"Exam not found: {exam_id}"}

        course_id = exam["course_id"]
        exam_date = exam["exam_date"]

        # Update user_input if provided
        if user_input:
            self._store.upsert_exam_record(
                exam_id=exam_id,
                course_id=course_id,
                exam_date=exam_date,
                chapter_weights=exam.get("chapter_weights"),
                user_input=user_input,
            )

        # Get predicted weak concepts (pre-exam snapshot)
        snapshots = self._temporal.get_all_mastery(course_id)
        weak_snaps = [
            s for s in snapshots
            if s.urgency in ("critical", "decaying")
        ]
        weak_snaps.sort(key=lambda s: s.current_mastery)
        predicted_weak = [
            f"{s.concept_name} (掌握度 {s.current_mastery*100:.0f}%, "
            f"最后复习 {s.days_since_review:.0f} 天前, urgency={s.urgency})"
            for s in weak_snaps[:15]
        ]
        predicted_weak_text = "\n".join(
            f"  {i+1}. {w}" for i, w in enumerate(predicted_weak)
        ) if predicted_weak else "（无考前掌握度数据）"

        # LLM analysis
        try:
            from .llm_gateway import gateway
            prompt = RETROSPECTIVE_PROMPT.format(
                course_id=course_id,
                exam_date=exam_date,
                predicted_weak=predicted_weak_text,
                user_input=user_input or "学生未提供考后反馈。",
            )
            messages = [
                {"role": "system", "content": "你是一个学习策略分析师。返回 JSON only。"},
                {"role": "user", "content": prompt},
            ]
            output = gateway.chat(messages, temperature=0.3)
            data = json.loads(output.content)
        except Exception as exc:
            logger.warning(f"Retrospective LLM call failed: {exc}")
            data = {
                "predicted_weak": [s.concept_name for s in weak_snaps[:15]],
                "actual_hits": [],
                "blind_spots": [],
                "overestimated": [],
                "analysis_text": "无法生成分析报告（LLM 调用失败）。请稍后重试。",
                "retention_hook": "",
            }

        # Persist
        retrospective_id = f"retro__{exam_id}"
        self._store.upsert_retrospective(
            retrospective_id=retrospective_id,
            exam_id=exam_id,
            predicted_weak=json.dumps(data.get("predicted_weak", []), ensure_ascii=False),
            actual_hits=json.dumps(data.get("actual_hits", []), ensure_ascii=False),
            blind_spots=json.dumps(data.get("blind_spots", []), ensure_ascii=False),
            overestimated=json.dumps(data.get("overestimated", []), ensure_ascii=False),
            analysis_text=data.get("analysis_text", ""),
            retention_hook=data.get("retention_hook", ""),
        )

        # Build full result
        n_predicted = len(data.get("predicted_weak", []))
        n_hit = len(data.get("actual_hits", []))
        hit_rate = f"{n_hit}/{n_predicted}" if n_predicted > 0 else "N/A"

        logger.info(f"Retrospective generated: {retrospective_id} (hit rate: {hit_rate})")

        return {
            "retrospective_id": retrospective_id,
            "exam_id": exam_id,
            "course_id": course_id,
            "exam_date": exam_date,
            "predicted_weak": data.get("predicted_weak", []),
            "actual_hits": data.get("actual_hits", []),
            "blind_spots": data.get("blind_spots", []),
            "overestimated": data.get("overestimated", []),
            "analysis_text": data.get("analysis_text", ""),
            "retention_hook": data.get("retention_hook", ""),
            "hit_rate": hit_rate,
        }

    # ── Queries ──────────────────────────────────────────────────

    def get_retrospective(self, exam_id: str) -> dict[str, Any] | None:
        """获取已有的复盘报告。"""
        row = self._store.get_retrospective_for_exam(exam_id)
        if not row:
            return None
        result = dict(row)
        for field in ("predicted_weak", "actual_hits", "blind_spots", "overestimated"):
            raw = result.get(field, "[]")
            try:
                result[field] = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                result[field] = []
        return result

    def list_exams(self, course_id: str) -> list[dict[str, Any]]:
        """列出某课程的所有考试记录。"""
        return self._store.get_exams_for_course(course_id)
