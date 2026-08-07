"""Agents layer: the 14 research agents and the async orchestrator."""

from quant_platform.agents.orchestrator import (
    AgentOrchestrator,
    AgentOutcome,
    OrchestratorResult,
)
from quant_platform.agents.registry import AGENTS, AgentSpec, ResearchAgent, get_agent

__all__ = [
    "AGENTS",
    "AgentOrchestrator",
    "AgentOutcome",
    "AgentSpec",
    "OrchestratorResult",
    "ResearchAgent",
    "get_agent",
]
