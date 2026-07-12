"""
LLM Gateway — Nexus Phase L1

单例模式 LLM 配置网关，收敛 7 处硬编码 LLM 配置。

环境变量（优先级从高到低）：
  NEXUS_LLM_MODEL     — 模型名（litellm 格式），默认 "openai/deepseek-chat"
  NEXUS_LLM_API_KEY   — API key，默认读 .env NEXUS_LLM_API_KEY 或 QWEN_API_KEY 回退
  NEXUS_LLM_BASE_URL  — API base URL，默认 https://api.deepseek.com/v1

用法：
  from nexus.knowledge.llm_gateway import gateway
  model = gateway.model        # "openai/deepseek-chat"
  kwargs = gateway.litellm_kwargs  # {"api_key": "...", "api_base": "..."}
"""
from __future__ import annotations

import json
import requests
import logging
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from ..config import _load_dotenv as _load_env  # noqa: F401 — 触发 .env 加载，确保 gateway 读取到 NEXUS_LLM_* 配置
_load_env()

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "openai/deepseek-chat"
_DEFAULT_BASE = "https://api.deepseek.com/v1"


def _load_key_from_file() -> str | None:
    """从 data/QWEN_API_KEY.json 加载 key（向后兼容）。"""
    key_file = Path("data/QWEN_API_KEY.json")
    if not key_file.exists():
        return None
    try:
        data = json.loads(key_file.read_text())
        return data.get("QWEN_API_KEY") or data.get("api_key") or data.get("key")
    except (json.JSONDecodeError, OSError):
        return None

class LLMGatewayError(RuntimeError):
    pass

class LLMGateway:
    """LLM 配置单例。"""

    def __init__(self, timeout: int = 60) -> None:
        self._model: str | None = None
        self._api_key: str | None = None
        self._base_url: str | None = None
        self.timeout = timeout

    @dataclass
    class LLMResponse:
        content: str
        raw: dict[str, Any]

    @property
    def model(self) -> str:
        if self._model is None:
            dotenv_modelname = os.getenv("NEXUS_LLM_MODEL","").strip()
            #auto-wrap bare model names in litellm "openai/" prefix
            if dotenv_modelname and "/" not in dotenv_modelname:
                dotenv_modelname = f"openai/{dotenv_modelname}"

            self._model = dotenv_modelname or _DEFAULT_MODEL

        return self._model
    
    @property
    def api_key(self) -> str | None:
        if self._api_key is None:
            self._api_key = os.getenv("NEXUS_LLM_API_KEY") if os.getenv("NEXUS_LLM_API_KEY") else _load_key_from_file()

        return self._api_key
    


    @property
    def base_url(self) -> str:
        if self._base_url is None:
            env_url = os.getenv("NEXUS_LLM_BASE_URL", "").strip()
            # Auto-append /v1 if not present (DeepSeek and most OpenAI-compat APIs use it)
            if env_url and not env_url.rstrip("/").endswith("/v1"):
                env_url = env_url.rstrip("/") + "/v1"
            self._base_url = env_url or _DEFAULT_BASE
        return self._base_url

    @property
    def litellm_kwargs(self) -> dict[str, str]:
        """返回传给 litellm.completion(**kwargs) 的 extra 参数。"""
        return {"api_key": self.api_key or "", "api_base": self.base_url}
    

    @property
    def is_enabled(self) -> bool:
        """Whether outbound LLM calls are explicitly enabled.

        A configured API key is intentionally insufficient. This prevents a
        developer environment with a lingering key from making requests until
        ``NEXUS_LLM_ENABLED`` is set explicitly.
        """
        return os.getenv("NEXUS_LLM_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

    @property
    def is_configured(self) -> bool:
        """Whether calls may be made: enabled and an API key is available."""
        return self.is_enabled and bool(self.api_key)

    def reset(self) -> None:
        """清除缓存，强制下一次访问重新读环境变量（仅测试用）。"""
        self._model = None
        self._api_key = None
        self._base_url = None

    def chat(
            self,
            messages: list[dict[str, str]],
            temperature: float = 0.2,
            response_format: dict[str, str] | None = None,
            max_tokens: int | None = None
    ) -> LLMResponse:
        
        if not self.is_enabled:
            raise LLMGatewayError("LLM is disabled. Set NEXUS_LLM_ENABLED=1 to enable requests.")
        if not self.api_key:
            raise LLMGatewayError("LLM is not configured. Set NEXUS_LLM_API_KEY.")
        
        # 构造请求头
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # litellm 格式 "openai/deepseek-chat" → API 格式 "deepseek-chat"
        api_model = self.model.split("/", 1)[1] if "/" in self.model else self.model

        #组建请求体
        payload: dict[str, Any] = {
            "model": api_model,
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
            raise LLMGatewayError(f"LLM request failed: {exc}") from exc
        
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMGatewayError(f"LLM response missing out; {exc}") from exc
        
        return self.LLMResponse(content=content, raw=data)
        
        


# 全局单例
gateway = LLMGateway()
