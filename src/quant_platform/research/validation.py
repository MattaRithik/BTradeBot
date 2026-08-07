"""Thesis validation: bull/bear/risk/leakage debate + judge verdict.

The debate agents reason (LLM); the verdict arithmetic is Python. Contract
with the agents: the leakage agent argues direction=negative when it finds
evidence an agent could not have known by as_of_date — leakage_detected
forces REJECTED regardless of everything else (schema invariant). The judge
runs AFTER the debate and sees the four conclusions verbatim in its package.
Every decision is audited as VALIDATION_DECISION.
"""

from __future__ import annotations

from quant_platform.agents.orchestrator import AgentOrchestrator
from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType, Direction, ValidationStatus
from quant_platform.core.schemas import (
    AgentArgument,
    EvidencePackage,
    SectorThesis,
    ValidationResult,
)
from quant_platform.models.provider import ModelProvider

_DEBATE_AGENTS = ["bull", "bear", "risk", "leakage"]

_LEAKAGE_CONFIDENCE = 0.5  # leakage agent must be at least this sure to trip
_APPROVE_CONFIDENCE = 0.6  # judge must be at least this confident to approve
_WEAK_CONFIDENCE = 0.4  # below this the judge is saying "not enough to decide"


def _is_leakage(argument: AgentArgument | None) -> bool:
    return (
        argument is not None
        and argument.direction == Direction.NEGATIVE
        and argument.confidence >= _LEAKAGE_CONFIDENCE
    )


def _decide_status(
    judge: AgentArgument | None,
    leakage_detected: bool,
    missing_evidence: list[str],
) -> ValidationStatus:
    if leakage_detected:
        return ValidationStatus.REJECTED
    if judge is None:
        return ValidationStatus.NEEDS_MORE_EVIDENCE
    if judge.direction == Direction.NEGATIVE:
        return ValidationStatus.REJECTED
    if judge.confidence < _WEAK_CONFIDENCE or missing_evidence:
        return ValidationStatus.NEEDS_MORE_EVIDENCE
    if judge.direction == Direction.POSITIVE and judge.confidence >= _APPROVE_CONFIDENCE:
        return ValidationStatus.APPROVED
    return ValidationStatus.WATCHLIST


async def validate_thesis(
    thesis: SectorThesis,
    package: EvidencePackage,
    provider: ModelProvider,
    audit: AuditLogger | None = None,
    max_concurrency: int | None = None,
) -> ValidationResult:
    """Run the validation debate for one thesis and return a verdict."""
    orchestrator = AgentOrchestrator(provider, audit=audit, max_concurrency=max_concurrency)

    debate = await orchestrator.run(package, agent_names=_DEBATE_AGENTS)
    bull = debate.arguments.get("bull")
    bear = debate.arguments.get("bear")
    risk = debate.arguments.get("risk")
    leakage = debate.arguments.get("leakage")
    leakage_detected = _is_leakage(leakage)

    # the judge sees the debate verbatim (as package warnings) plus the evidence
    debate_lines = [
        f"{name.upper()} (conf={arg.confidence:.2f}, {arg.direction.value}): {arg.conclusion}"
        for name, arg in [("bull", bull), ("bear", bear), ("risk", risk), ("leakage", leakage)]
        if arg is not None
    ]
    judge_package = package.model_copy(
        update={"warnings": [*package.warnings, *debate_lines]}
    )
    judged = await orchestrator.run(judge_package, agent_names=["judge"])
    judge = judged.arguments.get("judge")

    missing = sorted({m for arg in [bull, bear, judge] if arg for m in arg.missing_evidence})
    status = _decide_status(judge, leakage_detected, missing)
    score = judge.confidence if judge is not None else 0.0

    result = ValidationResult(
        thesis_id=thesis.thesis_id,
        status=status,
        bull=bull,
        bear=bear,
        risk=risk,
        leakage=leakage,
        judge_rationale=judge.conclusion if judge else "judge unavailable",
        leakage_detected=leakage_detected,
        score=score,
        as_of_date=package.as_of_date,
    )
    if audit is not None:
        audit.record(
            AuditEventType.VALIDATION_DECISION,
            run_id=package.run_id,
            as_of_date=package.as_of_date.isoformat(),
            thesis_id=thesis.thesis_id,
            status=result.status.value,
            score=score,
            leakage_detected=leakage_detected,
        )
    return result
