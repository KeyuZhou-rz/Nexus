"""Compatibility client backed by the unified LLM gateway."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..core.config import AppConfig
from ..knowledge.llm_gateway import (
    LLMGateway,
    LLMGatewayError,
    LLMResponse,
)

logger = logging.getLogger(__name__)


class LLMError(LLMGatewayError):
    """Backward-compatible briefing error type."""


class LLMClient:
    def __init__(self, config: AppConfig, sqlite_store: Any | None = None) -> None:
        self._store = sqlite_store
        self._gateway = LLMGateway(
            timeout=config.llm_timeout,
            enabled=config.llm_enabled,
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            model=config.llm_model,
        )
        self.model = config.llm_model or self._gateway.model

    def is_configured(self) -> bool:
        return self._gateway.is_configured

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        try:
            return self._gateway.chat(
                messages,
                temperature=temperature,
                response_format=response_format,
                max_tokens=max_tokens,
                call_site="briefing",
            )
        except LLMGatewayError as exc:
            raise LLMError(str(exc)) from exc

    def log_interaction(
        self,
        session_id: str,
        user_query: str,
        llm_response: str,
        *,
        course_id: str | None = None,
        model_used: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        call_site: str = "briefing",
    ) -> None:
        if self._store is None:
            return
        try:
            self._store.log_interaction(
                session_id=session_id,
                user_query=user_query,
                llm_response=llm_response,
                course_id=course_id,
                chunks_used=[],
                model_used=model_used or self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                call_site=call_site,
            )
        except Exception as exc:
            logger.warning("Briefing interaction_logs write failed: %s", exc)


def extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])
