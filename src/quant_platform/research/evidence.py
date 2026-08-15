"""Evidence engine: news → EvidenceCard extraction.

The LLM does the language work (reading headlines, judging category and
direction); Python does everything else: provenance, point-in-time fields,
and id assignment. Every card cites the news item it came from
(``source_ref = news_id``); a card that references an unknown news id is
dropped, never silently kept. ``published_at``/``usable_from`` are copied
from the source news item, so a card can never become visible before the
information it is based on.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from quant_platform.core.enums import (
    Direction,
    EvidenceCategory,
    PlatformModel,
)
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import EvidenceCard, NewsRecord
from quant_platform.models.provider import ModelProvider, ModelRequest


class EvidenceCardPayload(PlatformModel):
    """What the model is allowed to decide about one news item."""

    news_id: str
    claim: str
    category: EvidenceCategory
    direction: Direction = Direction.NEUTRAL
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    relevance: float = Field(ge=0.0, le=1.0, default=0.5)
    securities: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)


class EvidenceExtraction(PlatformModel):
    """Structured response contract for the extraction call."""

    cards: list[EvidenceCardPayload] = Field(default_factory=list)


_SYSTEM_PROMPT = (
    "You extract structured, citable evidence from news items for an "
    "institutional research platform. You operate CLOSED-BOOK: only the "
    "supplied items are admissible. For each MATERIAL item emit one card: "
    "the news_id it came from (never invent ids), a concise claim, the best "
    "category, the direction for the affected securities/sectors, your "
    "confidence, and the securities/sectors explicitly mentioned. Skip "
    "non-material noise. Never assert facts not present in the item."
)

_BODY_SNIPPET_CHARS = 400  # title + bounded summary/body, never full dumps
_BATCH_CHAR_BUDGET = 24_000  # ~6k tokens per extraction call


def _render_news(news: list[NewsRecord]) -> str:
    lines = []
    for item in news:
        snippet = (item.body or "").strip()[:_BODY_SNIPPET_CHARS]
        lines.append(
            f"- {item.news_id} [{item.published_at.date().isoformat()}] "
            f"securities={','.join(item.securities) or '-'} "
            f"sectors={','.join(item.sectors) or '-'} :: {item.headline}"
            + (f"\n  {snippet}" if snippet else "")
        )
    return "\n".join(lines)


def batch_news(news: list[NewsRecord], char_budget: int = _BATCH_CHAR_BUDGET) -> list[list[NewsRecord]]:
    """Pack news items into batches under an approximate token budget
    (chars/4 ≈ tokens), so prompts stay bounded regardless of feed volume."""
    batches: list[list[NewsRecord]] = []
    current: list[NewsRecord] = []
    size = 0
    for item in news:
        item_chars = len(item.headline) + min(len(item.body or ""), _BODY_SNIPPET_CHARS) + 80
        if current and size + item_chars > char_budget:
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += item_chars
    if current:
        batches.append(current)
    return batches


class EvidenceEngine:
    """Extracts EvidenceCards from gatekeeper-filtered news via a ModelProvider."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    async def extract(
        self,
        news: list[NewsRecord],
        as_of_date: date,
    ) -> list[EvidenceCard]:
        """One extraction call per token-budgeted batch; Python assigns provenance."""
        cards: list[EvidenceCard] = []
        for batch in batch_news(news):
            cards.extend(await self._extract_batch(batch))
        return cards

    async def _extract_batch(self, news: list[NewsRecord]) -> list[EvidenceCard]:
        if not news:
            return []
        request = ModelRequest(
            task="evidence_extraction",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_render_news(news),
            response_schema=EvidenceExtraction.model_json_schema(),
            response_model=EvidenceExtraction,
        )
        response = await self.provider.complete(request)
        extraction = response.structured
        if not isinstance(extraction, EvidenceExtraction):
            return []

        by_id: dict[str, NewsRecord] = {n.news_id: n for n in news}
        cards: list[EvidenceCard] = []
        for payload in extraction.cards:
            source = by_id.get(payload.news_id)
            if source is None:
                continue  # hallucinated citation — dropped, never kept
            cards.append(
                EvidenceCard(
                    evidence_id=stable_id("ev", payload.news_id, payload.claim),
                    source=source.source,
                    source_ref=source.news_id,
                    published_at=source.published_at,
                    usable_from=source.usable_from,
                    securities=payload.securities or list(source.securities),
                    sectors=payload.sectors or list(source.sectors),
                    category=payload.category,
                    direction=payload.direction,
                    confidence=payload.confidence,
                    relevance=payload.relevance,
                    claim=payload.claim,
                )
            )
        return cards


def group_evidence_by_sector(cards: list[EvidenceCard]) -> dict[str, list[EvidenceCard]]:
    """Group cards by sector label (a card with several sectors joins each)."""
    grouped: dict[str, list[EvidenceCard]] = {}
    for card in cards:
        for sector in card.sectors:
            grouped.setdefault(sector, []).append(card)
    return grouped


def evidence_stats(cards: list[EvidenceCard]) -> dict[str, Any]:
    """Small deterministic summary used by scoring and tests."""
    if not cards:
        return {"count": 0, "mean_confidence": 0.0, "mean_relevance": 0.0}
    return {
        "count": len(cards),
        "mean_confidence": sum(c.confidence for c in cards) / len(cards),
        "mean_relevance": sum(c.relevance for c in cards) / len(cards),
    }
