from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from ..config import AppConfig


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any]


class LLMClient:
    def __init__(self, config: AppConfig) -> None:
        self.base_url = (config.llm_base_url or "").rstrip("/")
        self.api_key = config.llm_api_key or ""
        self.model = config.llm_model or ""
        self.timeout = config.llm_timeout

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

        payload = {
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


def extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])
