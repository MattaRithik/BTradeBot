"""AgentOrchestrator: async fan-out/fan-in over a shared ModelProvider."""

from __future__ import annotations

import asyncio

import pytest
from tests.conftest import AS_OF, make_evidence, make_news

from quant_platform.agents import AGENTS, AgentOrchestrator, get_agent
from quant_platform.core.enums import AuditEventType
from quant_platform.core.schemas import AgentArgument, EvidencePackage
from quant_platform.models import MockModelProvider, ModelRequest, ModelResponse


@pytest.fixture()
def package() -> EvidencePackage:
    return EvidencePackage(
        run_id="run1",
        as_of_date=AS_OF,
        evidence=[make_evidence()],
        news=[make_news()],
        market_features_ref="features/run1.parquet",
        warnings=["synthetic data"],
    )


class TestFanOut:
    async def test_all_14_agents_succeed(self, package: EvidencePackage):
        orchestrator = AgentOrchestrator(MockModelProvider())
        result = await orchestrator.run(package)
        assert result.all_ok
        assert set(result.arguments) == set(AGENTS)
        assert len(result.arguments) == 14
        for name, arg in result.arguments.items():
            assert isinstance(arg, AgentArgument)
            assert arg.agent_name == name  # identity enforced, not model-supplied
            assert arg.as_of_date == AS_OF  # package date overwrites the canned one

    async def test_subset_of_agents(self, package: EvidencePackage):
        orchestrator = AgentOrchestrator(MockModelProvider())
        result = await orchestrator.run(package, agent_names=["bull", "bear"])
        assert result.all_ok
        assert set(result.arguments) == {"bull", "bear"}

    async def test_unknown_agent_isolated_failure(self, package: EvidencePackage):
        orchestrator = AgentOrchestrator(MockModelProvider())
        result = await orchestrator.run(package, agent_names=["bull", "nope"])
        assert not result.all_ok
        assert result.arguments["bull"].agent_name == "bull"
        assert "unknown agent" in result.failures["nope"]

    async def test_routing_keys_resolve_in_models_yaml(self):
        # every agent's routing key must exist in configs/models.yaml routing
        from quant_platform.core.config import load_yaml_config

        routing = load_yaml_config("models").get("routing", {})
        for name in AGENTS:
            assert get_agent(name).routing_key in routing, f"{name} has no routing entry"


class TestFailureIsolation:
    async def test_one_failure_does_not_cancel_others(self, package: EvidencePackage):
        provider = MockModelProvider(scripted={"bear": RuntimeError("boom")})
        orchestrator = AgentOrchestrator(provider)
        result = await orchestrator.run(package, agent_names=["bull", "bear", "risk"])
        assert not result.all_ok
        assert "boom" in result.failures["bear"]
        assert set(result.arguments) == {"bull", "risk"}


class _ConcurrencyTracker(MockModelProvider):
    """Records the maximum number of concurrently in-flight complete() calls."""

    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)  # force overlap when unbounded
            return await super().complete(request)
        finally:
            self.in_flight -= 1


class TestConcurrency:
    async def test_limit_respected(self, package: EvidencePackage):
        provider = _ConcurrencyTracker()
        orchestrator = AgentOrchestrator(provider, max_concurrency=2)
        result = await orchestrator.run(package)  # all 14 agents
        assert result.all_ok
        assert provider.max_in_flight <= 2

    async def test_default_comes_from_gateway_config(self):
        orchestrator = AgentOrchestrator(MockModelProvider())
        assert orchestrator.max_concurrency == 8  # configs/models.yaml gateway


class TestAuditTrail:
    async def test_started_finished_audited_per_agent(self, package: EvidencePackage, audit):
        orchestrator = AgentOrchestrator(MockModelProvider(), audit=audit)
        await orchestrator.run(package, agent_names=["macro", "risk"])
        assert audit.count_by_type(AuditEventType.AGENT_STARTED) == 2
        assert audit.count_by_type(AuditEventType.AGENT_FINISHED) == 2
        events = [e for e in audit.read_all() if e["event"] == AuditEventType.AGENT_FINISHED.value]
        assert all(e["details"]["ok"] for e in events)
        assert {e["details"]["agent"] for e in events} == {"macro", "risk"}
        assert all(e["run_id"] == "run1" for e in events)

    async def test_failure_audited_with_error(self, package: EvidencePackage, audit):
        provider = MockModelProvider(scripted={"judge": RuntimeError("verdict crashed")})
        orchestrator = AgentOrchestrator(provider, audit=audit)
        await orchestrator.run(package, agent_names=["judge"])
        events = [e for e in audit.read_all() if e["event"] == AuditEventType.AGENT_FINISHED.value]
        assert len(events) == 1
        assert events[0]["details"]["ok"] is False
        assert "verdict crashed" in events[0]["details"]["error"]
