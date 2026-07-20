"""
Knowledge LLM Client — Nexus Phase 3

委托统一的 LLMGateway 完成模型调用、配置解析与调用审计。
LLMGateway 支持 OpenAI 兼容接口，默认读取 NEXUS_LLM_MODEL /
NEXUS_LLM_API_KEY / NEXUS_LLM_BASE_URL 环境变量，并回退到
legacy 的 data/QWEN_API_KEY.json。

主要功能：
- chat(): 发送消息列表，返回回答文本
- 自动记录 interaction_logs（token 用量、使用模型、chunks 引用、call_site）

模型配置：通过 LLMGateway 单例读取；本类也可通过 model 参数或
NEXUS_QA_MODEL 环境变量覆盖默认模型（优先于网关默认）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .llm_gateway import gateway

logger = logging.getLogger(__name__)


@dataclass
class LLMOutput:
    """LLM 生成结果。"""
    content: str
    model_used: str
    input_tokens: int | None
    output_tokens: int | None


class KnowledgeLLMClient:
    """
    薄代理层：将问答调用委托给 LLMGateway，并负责写入 interaction_logs。

    参数：
    - sqlite_store: SQLiteStore 实例，用于记录 interaction_logs
    - model: 模型名（默认读取 NEXUS_QA_MODEL，其次使用网关默认模型）
    - temperature: 生成温度（问答任务建议 0.3-0.5）
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
        import os
        self.model = model or os.getenv("NEXUS_QA_MODEL") or gateway.model
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
        实际请求由 LLMGateway.chat() 完成。
        """
        effective_model = model or self.model

        response = gateway.chat(
            messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            call_site="qa",
            model=effective_model,
        )
        return LLMOutput(
            content=response.content,
            model_used=response.model_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    def log_interaction(
        self,
        session_id: str,
        user_query: str,
        llm_response: str,
        output: LLMOutput,
        *,
        course_id: str | None = None,
        chunks_used: list[str] | None = None,
        call_site: str = "qa_client",
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
                call_site=call_site,
            )
        except Exception as exc:
            # 日志写入失败不应中断问答流程
            logger.warning(f"interaction_logs 写入失败: {exc}")
