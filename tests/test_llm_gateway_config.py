from __future__ import annotations

import pytest

from nexus.knowledge.llm_gateway import LLMGateway, LLMGatewayError


def test_gateway_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("NEXUS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "0")
    gateway = LLMGateway()
    assert gateway.is_enabled is False
    assert gateway.is_configured is False
    with pytest.raises(LLMGatewayError, match="NEXUS_LLM_ENABLED"):
        gateway.chat([{"role": "user", "content": "hi"}])


def test_gateway_enabled_requires_key(monkeypatch):
    monkeypatch.setattr("nexus.knowledge.llm_gateway._load_key_from_file", lambda: None)
    monkeypatch.delenv("NEXUS_LLM_API_KEY", raising=False)
    monkeypatch.setenv("NEXUS_LLM_ENABLED", "true")
    gateway = LLMGateway()
    assert gateway.is_enabled is True
    assert gateway.is_configured is False
    with pytest.raises(LLMGatewayError, match="NEXUS_LLM_API_KEY"):
        gateway.chat([{"role": "user", "content": "hi"}])
