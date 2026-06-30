from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import requests

from ..config import AppConfig

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any]


class LLMClient:
    def __init__(
        self,
        config: AppConfig,
        sqlite_store: Any | None = None,
    ) -> None:
        self.base_url = (config.llm_base_url or "").rstrip("/")
        self.api_key = config.llm_api_key or ""
        self.model = config.llm_model or ""
        self.timeout = config.llm_timeout
        self._store = sqlite_store

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model)

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMError("LLM not configured. Set NEXUS_LLM_BASE_URL and NEXUS_LLM_MODEL.")

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens

        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response missing content.") from exc

        return LLMResponse(content=content, raw=data)

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
    ) -> None:
        """将简报 LLM 调用写入 SQLite interaction_logs。"""
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
