"""Normalized news-article schema for the news intelligence layer.

A NewsArticle is the provider-neutral, normalized form of one news item.
It carries NEWS ONLY — never prices, returns, fundamentals or bars (market
data is owned by the Bloomberg layer). NLP enrichment fields (organizations,
people, locations, sentiment) are OPTIONAL: the pipeline must work when they
are absent (e.g. older historical data without NLP).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from quant_platform.core.enums import PlatformModel
from quant_platform.core.timeutil import UtcDatetime


class NewsArticle(PlatformModel, frozen=True):
    """One normalized news article with deterministic dedup/rank metadata."""

    article_id: str
    provider: str  # e.g. "newscatcher"
    published_at: UtcDatetime
    retrieved_at: UtcDatetime  # original fetch time — cache hits preserve it
    title: str
    summary: str = ""
    content: str = ""
    source_name: str = ""
    source_domain: str = ""
    url: str = ""
    language: str = ""
    country: str = ""
    # OPTIONAL NLP enrichment — never required, never relied upon
    organizations: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    sentiment: float | None = None  # NEVER used for ranking
    # deterministic assignment (filled by the news-intel layer, not providers)
    sector_tags: list[str] = Field(default_factory=list)
    securities: list[str] = Field(default_factory=list)
    # provenance + dedup metadata
    raw_provider_id: str = ""
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str  # sha256 of normalized title + summary
    cluster_id: str = ""  # set on representatives of multi-article clusters
    duplicate_of: str = ""  # set on dropped duplicates (kept off representatives)
    source_confirmation: int = 1  # cluster size: repeated story, NOT independent evidence
