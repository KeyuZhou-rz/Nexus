"""
Session Manager — Nexus Phase 3

Session 粒度的学习记录追踪和 Profile 更新。

核心规则（来自 spec）：
- NEVER per-turn 更新 profile，ONLY session 粒度
- Session 结束触发条件：用户 idle 5 分钟 OR 显式调用 end_session()
- 流程：拉取 session logs → 构造 transcript → LLM 分析 → apply patch

Session 生命周期：
  start_session(session_id)
    ↓ 用户提问（QAPipeline.ask() 自动记录 interaction_logs）
    ↓ 用户提问 ...
    ↓ idle 5 分钟 或 显式结束
  end_session(session_id) → LLM 分析 → UserProfileManager.apply_patch()

实现细节：
- 活跃 session 记录在内存（dict），进程重启后不恢复（设计如此）
- LLM 分析使用 PROFILE_UPDATE_PROMPT（已定义于 prompts.py）
- 分析模型：轻量的 Gemini Flash（成本低，session 每天通常只有 1-2 次）
- 无 LLM 时 fallback：跳过 profile 更新，仅清理 session 状态
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .prompts import PROFILE_UPDATE_PROMPT
from .sqlite_store import SQLiteStore
from .user_profile import UserProfileManager

logger = logging.getLogger(__name__)

# Session idle 超时：超过此时间不活动则视为 session 结束
IDLE_TIMEOUT_MINUTES = 5


@dataclass
class SessionState:
    """单个 session 的内存状态。"""
    session_id: str
    started_at: datetime
    last_active: datetime
    course_id: str | None = None        # 本 session 主要使用的课程
    interaction_count: int = 0          # 本 session 累计问答次数
    ended: bool = False                 # 是否已结束（防止重复分析）


class SessionManager:
    """
    Session 生命周期管理器。

    参数：
    - sqlite_store: SQLiteStore 实例
    - profile_manager: UserProfileManager 实例
    - gemini_api_key: Gemini API key（用于 profile 更新分析）
    - idle_timeout_minutes: idle 超时分钟数（默认 5 分钟）
    """

    def __init__(
        self,
        sqlite_store: SQLiteStore,
        profile_manager: UserProfileManager,
        gemini_api_key: str | None = None,
        idle_timeout_minutes: int = IDLE_TIMEOUT_MINUTES,
    ) -> None:
        self._store = sqlite_store
        self._profile = profile_manager
        self._api_key = (
            gemini_api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
        )
        self._idle_timeout = timedelta(minutes=idle_timeout_minutes)

        # 内存中的活跃 session 表，key=session_id
        self._sessions: dict[str, SessionState] = {}

    # ────────────────────────────────────────────────
    # Session 生命周期
    # ────────────────────────────────────────────────

    def start_session(
        self,
        session_id: str,
        course_id: str | None = None,
    ) -> SessionState:
        """
        注册一个新 session。
        如果 session_id 已存在（重入），返回现有状态。
        """
        if session_id in self._sessions:
            state = self._sessions[session_id]
            state.last_active = datetime.now()
            return state

        state = SessionState(
            session_id=session_id,
            started_at=datetime.now(),
            last_active=datetime.now(),
            course_id=course_id,
        )
        self._sessions[session_id] = state
        logger.info(f"Session started: {session_id} (course={course_id})")
        return state

    def record_activity(
        self,
        session_id: str,
        course_id: str | None = None,
    ) -> None:
        """
        记录本 session 有新活动（QAPipeline.ask() 完成后调用）。
        更新 last_active 时间，用于 idle timeout 检测。
        """
        if session_id not in self._sessions:
            self.start_session(session_id, course_id)
            return

        state = self._sessions[session_id]
        state.last_active = datetime.now()
        state.interaction_count += 1
        if course_id and not state.course_id:
            state.course_id = course_id

    def is_idle(self, session_id: str) -> bool:
        """
        检查 session 是否超过 idle timeout（可由外部定时任务调用）。
        未注册的 session 视为已结束（idle=True）。
        """
        state = self._sessions.get(session_id)
        if state is None or state.ended:
            return True
        return (datetime.now() - state.last_active) >= self._idle_timeout

    def end_session(self, session_id: str) -> dict[str, Any]:
        """
        显式结束 session，触发 Profile 更新分析。

        流程：
        1. 从 SQLite interaction_logs 拉取本 session 所有 Q&A
        2. 构建对话摘要（transcript）
        3. 调用 LLM 分析，输出 profile patch
        4. 原子应用 patch 到 UserProfileManager
        5. 清理内存状态

        返回：patch 应用结果的摘要 dict
        """
        state = self._sessions.get(session_id)
        if state is None:
            return {"status": "not_found", "session_id": session_id}
        if state.ended:
            return {"status": "already_ended", "session_id": session_id}

        state.ended = True
        logs = self._store.get_session_logs(session_id)

        # 少于 2 轮问答时不分析（数据太少，LLM 分析价值低）
        if len(logs) < 2:
            logger.info(f"Session {session_id} 交互太少（{len(logs)} 条），跳过 profile 更新")
            del self._sessions[session_id]
            return {"status": "skipped", "reason": "too_few_interactions", "count": len(logs)}

        logger.info(f"Session {session_id} 结束，开始分析 {len(logs)} 条交互记录")

        # ── 构建对话 transcript ──
        transcript = _build_transcript(logs)

        # ── LLM 分析 ──
        patch = self._analyze_session(transcript)

        # ── 应用 patch ──
        if patch:
            result = self._profile.apply_patch(patch)
            logger.info(f"Profile 已更新: {result['applied']} | {result.get('notes', '')}")
        else:
            result = {"applied": "0 changes", "details": [], "notes": "LLM 分析跳过"}

        del self._sessions[session_id]
        return {
            "status": "completed",
            "session_id": session_id,
            "interactions": len(logs),
            "patch_result": result,
        }

    def check_and_end_idle_sessions(self) -> list[str]:
        """
        检查所有活跃 session，对超时的自动结束。
        可由 Streamlit 的定时轮询或后台线程周期性调用。
        返回：本次结束的 session_id 列表。
        """
        ended: list[str] = []
        for session_id in list(self._sessions.keys()):
            if self.is_idle(session_id):
                logger.info(f"Session {session_id} idle 超时，自动结束")
                self.end_session(session_id)
                ended.append(session_id)
        return ended

    # ────────────────────────────────────────────────
    # LLM 分析
    # ────────────────────────────────────────────────

    def _analyze_session(self, transcript: str) -> dict[str, Any] | None:
        """
        调用 LLM 分析 session transcript，返回 profile patch dict。
        无 LLM 可用时返回 None（跳过更新）。
        """
        if not self._api_key:
            logger.info("无 GEMINI_API_KEY，跳过 session LLM 分析")
            return None

        current_profile = self._profile.get()
        prompt = PROFILE_UPDATE_PROMPT.format(
            current_profile=json.dumps(current_profile, ensure_ascii=False, indent=2),
            session_transcript=transcript,
        )

        # 先尝试 litellm，再 fallback Gemini 直连
        try:
            return self._call_llm_litellm(prompt)
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f"litellm session 分析失败: {exc}")

        try:
            return self._call_llm_gemini(prompt)
        except Exception as exc:
            logger.warning(f"Gemini session 分析失败: {exc}")
            return None

    def _call_llm_litellm(self, prompt: str) -> dict[str, Any]:
        """使用 litellm 调用 LLM（模型无关）。"""
        import litellm
        model = os.getenv("NEXUS_QA_MODEL") or "gemini/gemini-2.0-flash"
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,     # 分析任务要确定性
            max_tokens=1024,
        )
        raw = response.choices[0].message.content or ""
        return _parse_llm_json(raw)

    def _call_llm_gemini(self, prompt: str) -> dict[str, Any]:
        """Gemini 直连 fallback。"""
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=self._api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                max_output_tokens=1024,
            ),
        )
        return _parse_llm_json(response.text or "")


# ────────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────────

def _build_transcript(logs: list[dict[str, Any]]) -> str:
    """
    将 interaction_logs 记录格式化为 LLM 可读的对话摘要。
    格式：
      [Turn 1]
      Q: 用户问题
      A: AI 回答（前 300 字）

    限制：最多取最近 10 轮（防止 prompt 过长）
    """
    recent = logs[-10:]  # 取最近 10 轮
    lines: list[str] = []
    for i, log in enumerate(recent, 1):
        q = (log.get("user_query") or "").strip()
        a = (log.get("llm_response") or "").strip()
        # 截断过长的回答
        if len(a) > 300:
            a = a[:300] + "..."
        lines.append(f"[Turn {i}]\nQ: {q}\nA: {a}")
    return "\n\n".join(lines)


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """
    解析 LLM 输出的 JSON patch。
    自动去除 markdown 代码块包装，健壮地处理格式问题。
    """
    text = raw.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包装
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        # 尝试找最外层的 {...}
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass

    logger.warning(f"无法解析 LLM session 分析输出: {raw[:200]}")
    return {}
