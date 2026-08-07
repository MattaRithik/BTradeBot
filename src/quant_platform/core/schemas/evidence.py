"""News, evidence and causal-graph schemas."""

from __future__ import annotations

from pydantic import Field

from quant_platform.core.enums import (
    Direction,
    EvidenceCategory,
    PlatformModel,
    SourceType,
)
from quant_platform.core.timeutil import UtcDatetime


class NewsRecord(PlatformModel):
    """A point-in-time news/event item. Never fabricated; always sourced."""

    news_id: str
    source: SourceType
    source_ref: str = ""
    headline: str
    body: str = ""
    securities: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    event_time: UtcDatetime | None = None  # when the underlying event happened
    published_at: UtcDatetime  # when the item was published
    usable_from: UtcDatetime  # when agents may first see it (>= published_at)
    retrieved_at: UtcDatetime


class EvidenceCard(PlatformModel):
    """Structured, citable claim extracted (by an agent) from sourced material."""

    evidence_id: str
    source: SourceType
    source_ref: str = ""  # e.g. news_id, filing accession, Bloomberg story id
    published_at: UtcDatetime
    usable_from: UtcDatetime
    securities: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    category: EvidenceCategory
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    relevance: float = Field(ge=0.0, le=1.0)
    claim: str  # concise extracted claim
    raw_ref: str = ""


class CausalNode(PlatformModel):
    node_id: str
    label: str  # e.g. "HBM demand", "grid power bottleneck"
    node_type: str = "theme"  # theme | industry | company | macro
    evidence_ids: list[str] = Field(default_factory=list)


class CausalEdge(PlatformModel):
    from_node: str
    to_node: str
    relation: str  # e.g. "drives", "constrains", "benefits"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
