from __future__ import annotations

import pytest

from nexus.config import AppConfig
from nexus.intelligence.llm import LLMClient
from nexus.knowledge.llm_client import KnowledgeLLMClient
from nexus.knowledge.llm_gateway import LLMGateway, LLMGatewayError, LLMResponse


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self):
        return self._payload


def test_gateway_retries_transient_failure_and_audits(monkeypatch):
    calls = []
    responses = [
        _Response(503, {}),
        _Response(200, {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 2}}),
    ]

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("nexus.knowledge.llm_gateway.requests.post", fake_post)
    gateway = LLMGateway(
        enabled=True,
        api_key="key",
        base_url="http://localhost/v1",
        model="openai/test",
        max_retries=1,
        retry_backoff=0,
    )
    result = gateway.chat([{"role": "user", "content": "hi"}], call_site="unit")
    assert result.content == "ok"
    assert result.retry_count == 1
    assert len(calls) == 2
    assert gateway.audit_log[0].call_site == "unit"
    assert gateway.audit_log[0].success is True


def test_gateway_normalizes_error_and_records_failed_audit(monkeypatch):
    monkeypatch.setattr(
        "nexus.knowledge.llm_gateway.requests.post",
        lambda *args, **kwargs: _Response(400, {}),
    )
    gateway = LLMGateway(enabled=True, api_key="key", max_retries=0, retry_backoff=0)
    with pytest.raises(LLMGatewayError, match="LLM request failed"):
        gateway.chat([], call_site="failure")
    assert gateway.audit_log[-1].call_site == "failure"
    assert gateway.audit_log[-1].success is False


def test_structured_output_parses_json(monkeypatch):
    monkeypatch.setattr(
        "nexus.knowledge.llm_gateway.requests.post",
        lambda *args, **kwargs: _Response(
            200, {"choices": [{"message": {"content": '{"answer": 1}'}}]}
        ),
    )
    gateway = LLMGateway(enabled=True, api_key="key", retry_backoff=0)
    assert gateway.structured_output([], call_site="structured") == {"answer": 1}


def test_knowledge_client_delegates_to_gateway(monkeypatch):
    seen = {}

    def fake_chat(messages, **kwargs):
        seen.update(kwargs)
        return LLMResponse(content="answer", raw={}, model_used="test")

    monkeypatch.setattr("nexus.knowledge.llm_client.gateway.chat", fake_chat)
    client = KnowledgeLLMClient(model="openai/test")
    output = client.chat([])
    assert output.content == "answer"
    assert seen["call_site"] == "qa"


def test_briefing_client_uses_configured_gateway(monkeypatch, tmp_path):
    config = AppConfig(
        data_dir=tmp_path,
        llm_enabled=True,
        llm_api_key="key",
        llm_base_url="http://localhost/v1",
        llm_model="openai/test",
    )
    client = LLMClient(config)
    monkeypatch.setattr(
        client._gateway,
        "chat",
        lambda messages, **kwargs: LLMResponse(content="{}", raw={}, model_used="openai/test"),
    )
    assert client.is_configured()
    assert client.chat([]).content == "{}"
