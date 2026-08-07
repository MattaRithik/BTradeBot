"""MockModelProvider: deterministic, offline, zero-cost, schema-valid."""

from __future__ import annotations

from datetime import date

import pytest

from quant_platform.core.schemas import AgentArgument
from quant_platform.models import (
    BudgetExceededError,
    MockModelProvider,
    ModelOutputValidationError,
    ModelRequest,
    UsageTracker,
)


def _request(task: str = "macro", structured: bool = True) -> ModelRequest:
    return ModelRequest(
        task=task,
        system_prompt="you are a test agent",
        user_prompt="reason about this evidence",
        response_schema=AgentArgument.model_json_schema() if structured else None,
        response_model=AgentArgument if structured else None,
    )


class TestMockDeterminism:
    async def test_same_request_same_output(self):
        provider = MockModelProvider()
        r1 = await provider.complete(_request())
        r2 = await provider.complete(_request())
        assert r1.text == r2.text
        assert not r1.cached and not r2.cached

    async def test_plain_text_when_no_schema(self):
        provider = MockModelProvider()
        resp = await provider.complete(_request(structured=False))
        assert resp.text.startswith("[MOCK]")
        assert resp.structured is None


class TestMockStructuredOutput:
    async def test_agent_argument_schema_valid(self):
        provider = MockModelProvider()
        resp = await provider.complete(_request(task="bull"))
        assert isinstance(resp.structured, AgentArgument)
        arg = resp.structured
        assert arg.agent_name == "bull"
        assert 0.0 <= arg.confidence <= 1.0
        assert isinstance(arg.as_of_date, date)

    async def test_zero_cost_and_token_accounting(self):
        provider = MockModelProvider()
        resp = await provider.complete(_request())
        assert resp.cost_usd == 0.0
        assert resp.model == "mock"
        assert resp.prompt_tokens > 0
        assert resp.completion_tokens > 0
        assert provider.tracker.calls == 1
        assert provider.tracker.cost_usd == 0.0


class TestMockScripted:
    async def test_scripted_dict_by_task(self):
        scripted = {
            "risk": {
                "agent_name": "risk",
                "conclusion": "scripted bearish risk view",
                "confidence": 0.9,
                "direction": "negative",
                "as_of_date": "2024-12-31",
            }
        }
        provider = MockModelProvider(scripted=scripted)
        resp = await provider.complete(_request(task="risk"))
        assert resp.structured.conclusion == "scripted bearish risk view"
        assert resp.structured.direction.value == "negative"

    async def test_scripted_exception_raises(self):
        provider = MockModelProvider(scripted={"judge": RuntimeError("boom")})
        with pytest.raises(RuntimeError, match="boom"):
            await provider.complete(_request(task="judge"))

    async def test_scripted_invalid_payload_raises_validation_error(self):
        provider = MockModelProvider(scripted={"bear": {"conclusion": 123}})
        with pytest.raises(ModelOutputValidationError):
            await provider.complete(_request(task="bear"))


class TestMockBudgetGuard:
    async def test_budget_guard_raises_before_call(self):
        tracker = UsageTracker(budget_usd_per_run=0.01)
        tracker.record(10, 10, 0.02)  # already over budget
        provider = MockModelProvider(tracker=tracker)
        with pytest.raises(BudgetExceededError):
            await provider.complete(_request())
        assert provider.calls == []  # refused before doing any work
