"""
LLM Gateway — Nexus Phase L1

单例模式 LLM 配置网关，收敛 7 处硬编码 Qwen 配置。

环境变量（优先级从高到低）：
  NEXUS_LLM_MODEL     — 模型名（litellm 格式），默认 "openai/qwen-plus"
  NEXUS_LLM_API_KEY   — API key，默认读 QWEN_API_KEY 或 data/QWEN_API_KEY.json
  NEXUS_LLM_BASE_URL  — API base URL，默认 https://dashscope.aliyuncs.com/compatible-mode/v1

用法：
  from nexus.knowledge.llm_gateway import gateway
  model = gateway.model        # "openai/qwen-plus"
  kwargs = gateway.litellm_kwargs  # {"api_key": "...", "api_base": "..."}
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_QWEN_DEFAULT_MODEL = "openai/qwen-plus"
_QWEN_DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


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


class LLMGateway:
    """LLM 配置单例。"""

    def __init__(self) -> None:
        self._model: str | None = None
        self._api_key: str | None = None
        self._base_url: str | None = None

    @property
    def model(self) -> str:
        if self._model is None:
            self._model = os.getenv("NEXUS_LLM_MODEL") or _QWEN_DEFAULT_MODEL
        return self._model

    @property
    def api_key(self) -> str | None:
        if self._api_key is None:
            self._api_key = (
                os.getenv("NEXUS_LLM_API_KEY")
                or os.getenv("QWEN_API_KEY")
                or _load_key_from_file()
            )
        return self._api_key

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = os.getenv("NEXUS_LLM_BASE_URL") or _QWEN_DEFAULT_BASE
        return self._base_url

    @property
    def litellm_kwargs(self) -> dict[str, str]:
        """返回传给 litellm.completion(**kwargs) 的 extra 参数。"""
        return {"api_key": self.api_key or "", "api_base": self.base_url}

    @property
    def is_configured(self) -> bool:
        """是否有可用的 API key。"""
        return bool(self.api_key)

    def reset(self) -> None:
        """清除缓存，强制下一次访问重新读环境变量（仅测试用）。"""
        self._model = None
        self._api_key = None
        self._base_url = None


# 全局单例
gateway = LLMGateway()
