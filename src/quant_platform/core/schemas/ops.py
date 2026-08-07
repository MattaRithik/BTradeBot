"""Operational schemas: failure analysis, model usage/cost tracking."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from quant_platform.core.enums import FailureType, PlatformModel
from quant_platform.core.timeutil import UtcDatetime


class FailureRecord(PlatformModel):
    """Post-mortem on an out-of-sample window. NEVER retroactively modifies
    the frozen prediction; informs future configuration only."""

    failure_id: str
    snapshot_id: str
    failure_type: FailureType
    what_was_predicted: str
    what_happened: str
    evidence_ids: list[str] = Field(default_factory=list)
    failed_component: str  # thesis | mapping | valuation | timing | execution | ...
    impact: str = ""
    lesson: str = ""
    suggested_improvement: str = ""
    as_of_date: date
    created_at: UtcDatetime


class ModelUsageRecord(PlatformModel):
    """One model-gateway call, for cost monitoring and budget guards."""

    usage_id: str
    run_id: str = ""
    provider: str  # kimi | mock
    model: str
    task_type: str  # agent name / call purpose
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    latency_ms: float = Field(ge=0, default=0.0)
    retry_count: int = Field(ge=0, default=0)
    cache_hit: bool = False
    estimated_cost_usd: float = Field(ge=0, default=0.0)
    created_at: UtcDatetime
