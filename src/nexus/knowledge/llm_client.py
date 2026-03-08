"""
Knowledge LLM Client — Nexus Phase 3

使用 litellm 作为 LLM 网关，实现模型无关的 API 调用。
litellm 支持 OpenAI / Gemini / Anthropic / Azure 等，一套接口切换模型。

主要功能：
- chat(): 发送消息列表，返回回答文本
- 自动记录 interaction_logs（token 用量、使用模型、chunks 引用）
- 若 litellm 未安装，fallback 到 google-generativeai 直接调用

模型配置（通过环境变量）：
  NEXUS_QA_MODEL=gpt-4o                # OpenAI（需 OPENAI_API_KEY）
  NEXUS_QA_MODEL=gemini/gemini-2.0-flash  # Gemini（需 GEMINI_API_KEY）
  NEXUS_QA_MODEL=claude-opus-4-6         # Anthropic（需 ANTHROPIC_API_KEY）
  默认: gemini/gemini-2.0-flash（Gemini Flash 成本最低）
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认模型（QWEN via DashScope OpenAI-compatible endpoint）
DEFAULT_MODEL = "openai/qwen-plus"
_QWEN_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass
class LLMOutput:
    """LLM 生成结果。"""
    content: str
    model_used: str
    input_tokens: int | None
    output_tokens: int | None


class KnowledgeLLMClient:
    """
    模型无关的 LLM 客户端，优先使用 litellm，fallback 到 Gemini 直连。

    参数：
    - sqlite_store: SQLiteStore 实例，用于记录 interaction_logs
    - model: litellm 格式模型名（默认读取 NEXUS_QA_MODEL 环境变量）
    - temperature: 生成温度（问答任务建议 0.3-0.5，不需要太确定性）
    - max_tokens: 单次回答最大 token 数
    """

    def __init__(
        self,
        sqlite_store: Any | None = None,   # SQLiteStore，可选（方便测试时传 None）
        model: str | None = None,
        temperature: float = 0.4,
        max_tokens: int = 1500,
    ) -> None:
        self._store = sqlite_store
        self.model = model or os.getenv("NEXUS_QA_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
    ) -> LLMOutput:
        """
        发送 messages 列表，返回 LLMOutput。
        优先使用 litellm，litellm 不可用时 fallback 到 Gemini 直连。
        """
        effective_model = model or self.model

        return self._chat_litellm(messages, effective_model)

    def log_interaction(
        self,
        session_id: str,
        user_query: str,
        llm_response: str,
        output: LLMOutput,
        *,
        course_id: str | None = None,
        chunks_used: list[str] | None = None,
    ) -> None:
        """
        将本次问答记录写入 SQLite interaction_logs。
        仅在 sqlite_store 可用时写入（方便测试时跳过）。
        """
        if self._store is None:
            return
        try:
            self._store.log_interaction(
                session_id=session_id,
                user_query=user_query,
                llm_response=llm_response,
                course_id=course_id,
                chunks_used=chunks_used or [],
                model_used=output.model_used,
                input_tokens=output.input_tokens,
                output_tokens=output.output_tokens,
            )
        except Exception as exc:
            # 日志写入失败不应中断问答流程
            logger.warning(f"interaction_logs 写入失败: {exc}")

    # ────────────────────────────────────────────────
    # 内部实现
    # ────────────────────────────────────────────────

    def _chat_litellm(
        self,
        messages: list[dict[str, str]],
        model: str,
    ) -> LLMOutput:
        """
        使用 litellm.completion 调用 LLM。
        litellm 自动处理不同 provider 的 API 格式差异。
        需要预先设置对应 provider 的 API Key 环境变量（如 GEMINI_API_KEY）。
        """
        import litellm  # 延迟导入，安装检测

        api_key = os.getenv("QWEN_API_KEY")
        extra = (
            {"api_key": api_key, "api_base": _QWEN_API_BASE}
            if api_key and model.startswith("openai/qwen")
            else {}
        )
        response = litellm.completion(
            model=model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            **extra,
        )

        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)

        return LLMOutput(
            content=content,
            model_used=model,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )

