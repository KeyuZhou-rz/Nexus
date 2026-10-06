"""Unified OpenAI-compatible LLM gateway."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from ..core.config import _load_dotenv as _load_env

_load_env()
logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "openai/deepseek-chat"
_DEFAULT_BASE = "https://api.deepseek.com/v1"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _load_key_from_file() -> str | None:
    """Load the legacy key file for backward compatibility."""
    key_file = Path("data/QWEN_API_KEY.json")
    if not key_file.exists():
        return None
    try:
        data = json.loads(key_file.read_text(encoding="utf-8"))
        return data.get("QWEN_API_KEY") or data.get("api_key") or data.get("key")
    except (json.JSONDecodeError, OSError):
        return None


class LLMGatewayError(RuntimeError):
    """Normalized error raised by every OpenAI-compatible LLM call."""


@dataclass
class LLMResponse:
    content: str
    raw: dict[str, Any]
    model_used: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    call_site: str = "unspecified"
    retry_count: int = 0
    latency_ms: int = 0


@dataclass
class LLMCallAudit:
    call_site: str
    model: str
    success: bool
    retry_count: int
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


class LLMGateway:
    """Configuration, transport, retry, structured output, and call audit."""

    # Preserve the old ``gateway.LLMResponse(...)`` construction contract.
    LLMResponse = LLMResponse

    def __init__(
        self,
        timeout: int = 60,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_retries: int = 2,
        retry_backoff: float = 0.25,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._enabled = enabled
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self._audit_log: list[LLMCallAudit] = []

    @property
    def model(self) -> str:
        if self._model is None:
            model = os.getenv("NEXUS_LLM_MODEL", "").strip()
            self._model = model or _DEFAULT_MODEL
        return self._model

    @property
    def api_key(self) -> str | None:
        if self._api_key is None:
            self._api_key = os.getenv("NEXUS_LLM_API_KEY") or _load_key_from_file()
        return self._api_key

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = os.getenv("NEXUS_LLM_BASE_URL", "").strip() or _DEFAULT_BASE
        url = self._base_url.rstrip("/")
        return url if url.endswith("/v1") else f"{url}/v1"

    @property
    def litellm_kwargs(self) -> dict[str, str]:
        """Compatibility for non-OpenAI native provider fallbacks."""
        return {"api_key": self.api_key or "", "api_base": self.base_url}

    @property
    def is_enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        return os.getenv("NEXUS_LLM_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

    @property
    def is_configured(self) -> bool:
        return self.is_enabled and bool(self.api_key)

    @property
    def audit_log(self) -> tuple[LLMCallAudit, ...]:
        return tuple(self._audit_log)

    def clear_audit_log(self) -> None:
        self._audit_log.clear()

    def reset(self) -> None:
        """Clear environment-derived values and audit state (primarily for tests)."""
        self._model = None
        self._api_key = None
        self._base_url = None
        self._enabled = None
        self.clear_audit_log()

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
        *,
        call_site: str = "unspecified",
        model: str | None = None,
        timeout: int | None = None,
    ) -> LLMResponse:
        if not self.is_enabled:
            raise LLMGatewayError("LLM is disabled. Set NEXUS_LLM_ENABLED=1 to enable requests.")
        if not self.api_key:
            raise LLMGatewayError("LLM is not configured. Set NEXUS_LLM_API_KEY.")

        effective_model = model or self.model
        api_model = effective_model.split("/", 1)[1] if "/" in effective_model else effective_model
        payload: dict[str, Any] = {
            "model": api_model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        started = time.monotonic()
        retries = 0
        try:
            while True:
                try:
                    response = requests.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=timeout or self.timeout,
                    )
                    if response.status_code in _RETRYABLE_STATUS and retries < self.max_retries:
                        retries += 1
                        time.sleep(self.retry_backoff * (2 ** (retries - 1)))
                        continue
                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if not isinstance(content, str):
                        raise TypeError("content is not a string")
                    usage = data.get("usage") or {}
                    latency = int((time.monotonic() - started) * 1000)
                    result = LLMResponse(
                        content=content,
                        raw=data,
                        model_used=effective_model,
                        input_tokens=usage.get("prompt_tokens"),
                        output_tokens=usage.get("completion_tokens"),
                        call_site=call_site,
                        retry_count=retries,
                        latency_ms=latency,
                    )
                    self._audit_log.append(LLMCallAudit(
                        call_site=call_site, model=effective_model, success=True,
                        retry_count=retries, latency_ms=latency,
                        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    ))
                    return result
                except (requests.Timeout, requests.ConnectionError) as exc:
                    if retries >= self.max_retries:
                        raise exc
                    retries += 1
                    time.sleep(self.retry_backoff * (2 ** (retries - 1)))
        except Exception as exc:
            latency = int((time.monotonic() - started) * 1000)
            error = exc if isinstance(exc, LLMGatewayError) else LLMGatewayError(f"LLM request failed: {exc}")
            self._audit_log.append(LLMCallAudit(
                call_site=call_site, model=effective_model, success=False,
                retry_count=retries, latency_ms=latency, error=str(error),
            ))
            raise error from exc

    def structured_output(
        self,
        messages: list[dict[str, str]],
        *,
        call_site: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        """Return parsed JSON from a normalized JSON-mode chat call."""
        response = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            call_site=call_site,
            model=model,
            timeout=timeout,
        )
        try:
            return json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise LLMGatewayError(f"LLM structured output is invalid JSON: {exc}") from exc


gateway = LLMGateway()
