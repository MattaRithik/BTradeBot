"""KimiProvider contract tests: fully offline via an injected fake async client.

Mirrors the blpapi contract-test pattern: the real network is never touched;
the provider must retry honestly, validate structured output, cache, account
cost, audit MODEL_CALL, and refuse when unauthenticated or over budget.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any

import httpx
import pytest

from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import EnvSettings
from quant_platform.core.enums import AuditEventType
from quant_platform.core.schemas import AgentArgument
from quant_platform.models import (
    BudgetExceededError,
    KimiProvider,
    ModelOutputValidationError,
    ModelProviderError,
    ModelRequest,
    UsageTracker,
)
from quant_platform.models.provider import DailyUsageLedger

_SECRET = "test-secret-key"


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if body is not None else "<not json>"

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise json.JSONDecodeError("no json", self.text, 0)
        return self._body


class FakeClient:
    """Plays back a script of FakeResponse / Exception items, records requests."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        self.requests.append({"url": url, "json": json})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _argument_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "agent_name": "macro",
        "conclusion": "liquidity is loosening",
        "confidence": 0.7,
        "direction": "positive",
        "evidence_ids": ["ev1"],
        "risks": ["policy reversal"],
        "missing_evidence": [],
        "as_of_date": "2024-12-31",
        "details": {},
    }
    payload.update(overrides)
    return payload


def _chat_body(content: Any, prompt_tokens: int = 10, completion_tokens: int = 5) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _settings(**overrides: Any) -> EnvSettings:
    return EnvSettings(kimi_api_key=_SECRET, kimi_model="kimi-test", **overrides)


def _gateway(**overrides: Any) -> dict[str, Any]:
    gw = {"max_retries": 3, "backoff_base_seconds": 0.0, "cache_enabled": True}
    gw.update(overrides)
    return gw


def _provider(client: FakeClient, **kwargs: Any) -> KimiProvider:
    kwargs.setdefault("settings", _settings())
    kwargs.setdefault("gateway", _gateway())
    kwargs.setdefault("client", client)
    # isolate the persistent cache + ledger per provider (no cross-test leakage)
    kwargs.setdefault("cache_dir", tempfile.mkdtemp(prefix="kimi_cache_"))
    kwargs.setdefault(
        "ledger", DailyUsageLedger(tempfile.mkdtemp(prefix="kimi_ledger_"), budget_usd_per_day=0.0)
    )
    return KimiProvider(**kwargs)


def _request(task: str = "macro", structured: bool = True) -> ModelRequest:
    return ModelRequest(
        task=task,
        system_prompt="you are a test agent",
        user_prompt="reason about this evidence",
        response_schema=AgentArgument.model_json_schema() if structured else None,
        response_model=AgentArgument if structured else None,
    )


class TestSuccessPath:
    async def test_structured_output_validated(self):
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))])
        provider = _provider(client)
        resp = await provider.complete(_request())
        assert isinstance(resp.structured, AgentArgument)
        assert resp.structured.conclusion == "liquidity is loosening"
        assert resp.prompt_tokens == 10
        assert resp.completion_tokens == 5
        assert resp.model == "kimi-test"
        assert not resp.cached

    async def test_request_payload_shape(self):
        client = FakeClient([FakeResponse(200, _chat_body("plain answer"))])
        provider = _provider(client)
        await provider.complete(_request(structured=False))
        call = client.requests[0]
        assert call["url"] == "/chat/completions"
        body = call["json"]
        assert body["model"] == "kimi-test"
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"
        assert "response_format" not in body  # no schema -> no JSON mode

    async def test_json_mode_requested_when_schema_given(self):
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))])
        provider = _provider(client)
        await provider.complete(_request())
        assert client.requests[0]["json"]["response_format"] == {"type": "json_object"}

    async def test_cost_accounting_from_pricing(self):
        pricing = {"kimi-test": {"input": 1.0, "output": 2.0}}
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))])
        provider = _provider(client, gateway=_gateway(pricing_usd_per_mtok=pricing))
        resp = await provider.complete(_request())
        # (10 * 1.0 + 5 * 2.0) / 1e6
        assert resp.cost_usd == pytest.approx(20 / 1_000_000)
        assert provider.tracker.cost_usd == pytest.approx(20 / 1_000_000)


class TestRetries:
    async def test_retry_then_success_on_429(self):
        client = FakeClient([
            FakeResponse(429, {"error": "rate limited"}),
            FakeResponse(200, _chat_body(_argument_payload())),
        ])
        provider = _provider(client)
        resp = await provider.complete(_request())
        assert isinstance(resp.structured, AgentArgument)
        assert len(client.requests) == 2

    async def test_retry_then_success_on_timeout(self):
        client = FakeClient([
            httpx.TimeoutException("slow"),
            FakeResponse(200, _chat_body(_argument_payload())),
        ])
        provider = _provider(client)
        resp = await provider.complete(_request())
        assert isinstance(resp.structured, AgentArgument)
        assert len(client.requests) == 2

    async def test_retries_exhausted_raises_honestly(self):
        client = FakeClient([FakeResponse(429, {"error": "rate limited"})] * 3)
        provider = _provider(client)
        with pytest.raises(ModelProviderError, match="after 3 attempt"):
            await provider.complete(_request())
        assert len(client.requests) == 3

    async def test_client_error_not_retried(self):
        client = FakeClient([FakeResponse(401, {"error": "bad key"})])
        provider = _provider(client)
        with pytest.raises(ModelProviderError, match="HTTP 401"):
            await provider.complete(_request())
        assert len(client.requests) == 1  # retrying a 4xx is pointless


class TestStructuredValidation:
    async def test_non_json_output_raises(self):
        client = FakeClient([FakeResponse(200, _chat_body("definitely not json"))])
        provider = _provider(client)
        with pytest.raises(ModelOutputValidationError, match="not valid JSON"):
            await provider.complete(_request())

    async def test_schema_violation_raises(self):
        client = FakeClient([FakeResponse(200, _chat_body({"conclusion": 123}))])
        provider = _provider(client)
        with pytest.raises(ModelOutputValidationError, match="schema validation"):
            await provider.complete(_request())

    async def test_missing_choices_raises(self):
        client = FakeClient([FakeResponse(200, {"unexpected": True})])
        provider = _provider(client)
        with pytest.raises(ModelProviderError, match="missing choices"):
            await provider.complete(_request(structured=False))


class TestCache:
    async def test_second_identical_call_is_cached(self):
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))])
        provider = _provider(client)
        first = await provider.complete(_request())
        second = await provider.complete(_request())
        assert len(client.requests) == 1  # no second HTTP call
        assert not first.cached
        assert second.cached
        assert second.cost_usd == 0.0
        assert provider.tracker.calls == 1  # cache hits skip cost accounting

    async def test_cache_disabled_hits_network_twice(self):
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))] * 2)
        provider = _provider(client, gateway=_gateway(cache_enabled=False))
        await provider.complete(_request())
        await provider.complete(_request())
        assert len(client.requests) == 2


class TestGuards:
    def test_missing_api_key_refused_at_construction(self):
        with pytest.raises(ModelProviderError, match="KIMI_API_KEY"):
            KimiProvider(settings=EnvSettings(kimi_api_key=""), gateway=_gateway())

    async def test_budget_guard_refuses_before_any_call(self):
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))])
        tracker = UsageTracker(budget_usd_per_run=0.01)
        tracker.record(10, 10, 0.02)  # already over budget
        provider = _provider(client, tracker=tracker)
        with pytest.raises(BudgetExceededError):
            await provider.complete(_request())
        assert client.requests == []  # refused before touching the network


class TestAudit:
    async def test_model_call_audited_without_secrets(self, tmp_path):
        audit = AuditLogger(tmp_path / "audit.jsonl")
        pricing = {"kimi-test": {"input": 1.0, "output": 2.0}}
        client = FakeClient([FakeResponse(200, _chat_body(_argument_payload()))])
        provider = _provider(
            client, audit=audit, run_id="run1", gateway=_gateway(pricing_usd_per_mtok=pricing)
        )
        await provider.complete(_request())
        events = audit.read_all()
        assert audit.count_by_type(AuditEventType.MODEL_CALL) == 1
        details = events[0]["details"]
        assert details["model"] == "kimi-test"
        assert details["prompt_tokens"] == 10
        assert details["cost_usd"] == pytest.approx(20 / 1_000_000)
        assert _SECRET not in json.dumps(events)  # the key never reaches the audit stream
