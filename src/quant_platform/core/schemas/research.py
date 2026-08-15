"""Research artifacts: theses, mappings, agent arguments, validation, ranking."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from quant_platform.core.enums import (
    Direction,
    ExposureType,
    PlatformModel,
    ValidationStatus,
)
from quant_platform.core.schemas.evidence import CausalEdge, CausalNode, EvidenceCard
from quant_platform.core.timeutil import UtcDatetime


class AgentArgument(PlatformModel):
    """Structured output contract every research agent must satisfy."""

    agent_name: str
    conclusion: str
    confidence: float = Field(ge=0.0, le=1.0)
    direction: Direction = Direction.NEUTRAL
    evidence_ids: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    as_of_date: date
    details: dict[str, str] = Field(default_factory=dict)


class SectorThesis(PlatformModel):
    thesis_id: str
    sector: str  # human-readable sector label — NOT a ticker
    trend_name: str
    thesis_summary: str
    demand_driver: str = ""
    causal_chain: list[CausalEdge] = Field(default_factory=list)
    causal_nodes: list[CausalNode] = Field(default_factory=list)
    bottlenecks: list[str] = Field(default_factory=list)
    beneficiary_industries: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    candidate_securities: list[str] = Field(default_factory=list)
    time_horizon_days: int = Field(default=63, gt=0)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    as_of_date: date
    created_at: UtcDatetime


class CompanyMapping(PlatformModel):
    sector: str
    ticker: str
    company_name: str = ""
    industry: str = ""
    exposure: ExposureType
    exposure_rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    as_of_date: date


class ETFMapping(PlatformModel):
    sector: str
    etf_ticker: str
    etf_name: str = ""
    exposure: ExposureType = ExposureType.INDIRECT
    relevance: float = Field(ge=0.0, le=1.0, default=0.5)
    as_of_date: date


class TradabilityResult(PlatformModel):
    ticker: str
    tradable: bool
    reasons: list[str] = Field(default_factory=list)  # why NOT tradable
    avg_dollar_volume: float | None = None
    history_days: int = 0
    last_price: float | None = None
    as_of_date: date


class EvidencePackage(PlatformModel):
    """Everything an agent run is allowed to see, assembled under the gatekeeper."""

    run_id: str
    as_of_date: date
    evidence: list[EvidenceCard] = Field(default_factory=list)
    news: list = Field(default_factory=list)  # list[NewsRecord]
    market_features_ref: str = ""  # pointer to feature artifact (provenance)
    # bounded, point-in-time feature VALUES the agent can actually reason over
    market_features: dict[str, dict[str, float]] = Field(default_factory=dict)
    context_block: str = ""  # sector/macro specialization context, rendered first
    warnings: list[str] = Field(default_factory=list)


class ValidationResult(PlatformModel):
    thesis_id: str
    status: ValidationStatus
    bull: AgentArgument | None = None
    bear: AgentArgument | None = None
    risk: AgentArgument | None = None
    leakage: AgentArgument | None = None
    judge_rationale: str = ""
    leakage_detected: bool = False  # if True, status MUST be REJECTED
    score: float = Field(ge=0.0, le=1.0, default=0.0)
    as_of_date: date


class ScoreBreakdown(PlatformModel):
    """Transparent, Python-computed component scores (all normalized 0..1).

    A component that could not be MEASURED (e.g. no PIT-safe fundamentals at
    a historical date) is 0.0 here and listed in ``missing_components`` — it
    is never silently treated as a measured neutral value. ``composite``
    renormalizes over measured components and applies the configured
    completeness penalty; ``data_completeness`` is the measured fraction.
    """

    trend_strength: float = Field(ge=0, le=1, default=0)
    evidence_quality: float = Field(ge=0, le=1, default=0)
    supply_chain_confidence: float = Field(ge=0, le=1, default=0)
    market_confirmation: float = Field(ge=0, le=1, default=0)
    fundamental_confirmation: float = Field(ge=0, le=1, default=0)
    valuation_risk: float = Field(ge=0, le=1, default=0)  # higher = riskier
    crowding_risk: float = Field(ge=0, le=1, default=0)
    liquidity: float = Field(ge=0, le=1, default=0)
    macro_alignment: float = Field(ge=0, le=1, default=0)
    validation_strength: float = Field(ge=0, le=1, default=0)
    composite: float = Field(ge=0, le=1, default=0)
    missing_components: list[str] = Field(default_factory=list)
    data_completeness: float = Field(ge=0, le=1, default=1.0)


class SectorSubmission(PlatformModel):
    """A sector's entry into the cross-sector competition."""

    thesis: SectorThesis
    validation: ValidationResult
    scores: ScoreBreakdown
    composite_score: float = Field(ge=0.0, le=1.0)


class RankedSector(PlatformModel):
    rank: int
    sector: str
    composite_score: float = Field(ge=0.0, le=1.0)
    validation_status: ValidationStatus
    selected: bool  # False when the system chooses nothing (weak evidence)
    rationale: str = ""


class RankingResult(PlatformModel):
    run_id: str
    as_of_date: date
    leaderboard: list[RankedSector] = Field(default_factory=list)
    selection_rationale: str = ""
