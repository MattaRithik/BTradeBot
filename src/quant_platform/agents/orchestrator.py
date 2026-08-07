"""Async fan-out/fan-in orchestrator for the research agents.

All selected agents run concurrently over a SHARED ModelProvider, bounded by
a semaphore at gateway.max_concurrency (configs/models.yaml). Failures are
isolated: one agent's exception never cancels the others — it is recorded in
that agent's AgentOutcome and audited. AGENT_STARTED / AGENT_FINISHED are
audited per agent with the run_id.
"""

from __future__ import annotations

import asyncio
from datetime import date

from quant_platform.agents.registry import AGENTS, ResearchAgent, get_agent
from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType, PlatformModel
from quant_platform.core.schemas import AgentArgument, EvidencePackage
from quant_platform.models.kimi import load_gateway_config
from quant_platform.models.provider import ModelProvider

_DEFAULT_CONCURRENCY = 8


class AgentOutcome(PlatformModel):
    """Per-agent result: exactly one of argument / error is set."""

    agent_name: str
    ok: bool
    argument: AgentArgument | None = None
    error: str = ""


class OrchestratorResult(PlatformModel):
    run_id: str
    as_of_date: date
    outcomes: dict[str, AgentOutcome]

    @property
    def arguments(self) -> dict[str, AgentArgument]:
        return {n: o.argument for n, o in self.outcomes.items() if o.argument is not None}

    @property
    def failures(self) -> dict[str, str]:
        return {n: o.error for n, o in self.outcomes.items() if not o.ok}

    @property
    def all_ok(self) -> bool:
        return all(o.ok for o in self.outcomes.values())


class AgentOrchestrator:
    """Runs a set of agents concurrently over one shared ModelProvider."""

    def __init__(
        self,
        provider: ModelProvider,
        audit: AuditLogger | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.provider = provider
        self.audit = audit
        if max_concurrency is None:
            max_concurrency = int(load_gateway_config().get("max_concurrency", _DEFAULT_CONCURRENCY))
        self.max_concurrency = max(1, max_concurrency)

    async def run(
        self,
        package: EvidencePackage,
        agent_names: list[str] | None = None,
    ) -> OrchestratorResult:
        names = list(agent_names) if agent_names is not None else sorted(AGENTS)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _one(name: str) -> AgentOutcome:
            try:
                spec = get_agent(name)
            except KeyError as exc:
                return AgentOutcome(agent_name=name, ok=False, error=str(exc))
            async with semaphore:
                if self.audit is not None:
                    self.audit.record(
                        AuditEventType.AGENT_STARTED,
                        run_id=package.run_id,
                        as_of_date=package.as_of_date.isoformat(),
                        agent=name,
                        routing_key=spec.routing_key,
                    )
                try:
                    argument = await ResearchAgent(spec, self.provider).run(package)
                except Exception as exc:
                    if self.audit is not None:
                        self.audit.record(
                            AuditEventType.AGENT_FINISHED,
                            run_id=package.run_id,
                            as_of_date=package.as_of_date.isoformat(),
                            agent=name,
                            ok=False,
                            error=str(exc)[:300],
                        )
                    return AgentOutcome(agent_name=name, ok=False, error=str(exc)[:500])
                if self.audit is not None:
                    self.audit.record(
                        AuditEventType.AGENT_FINISHED,
                        run_id=package.run_id,
                        as_of_date=package.as_of_date.isoformat(),
                        agent=name,
                        ok=True,
                        confidence=argument.confidence,
                        direction=argument.direction.value,
                    )
                return AgentOutcome(agent_name=name, ok=True, argument=argument)

        # every _one catches its own exceptions, so gather never cancels siblings
        outcomes = await asyncio.gather(*(_one(n) for n in names))
        return OrchestratorResult(
            run_id=package.run_id,
            as_of_date=package.as_of_date,
            outcomes={o.agent_name: o for o in outcomes},
        )
