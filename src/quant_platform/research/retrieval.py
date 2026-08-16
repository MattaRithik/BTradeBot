"""Retrieval index interface + deterministic local lexical implementation.

The architecture calls for semantic retrieval over evidence. There is NO fake
embedding API here: the default backend is a pure-Python TF-IDF cosine index
that runs fully offline and deterministically. A real embedding provider can
be added later behind the same ``RetrievalIndex`` protocol — the factory
refuses unknown backends honestly instead of silently degrading.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol, runtime_checkable

from pydantic import Field

from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import EvidenceCard

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _card_text(card: EvidenceCard) -> str:
    parts = [card.claim, *card.securities, *card.sectors, *card.themes, card.source_ref]
    return " ".join(p for p in parts if p)


class ScoredCard(PlatformModel):
    evidence_id: str
    score: float = Field(ge=0.0)
    card: EvidenceCard


@runtime_checkable
class RetrievalIndex(Protocol):
    """Bounded evidence retrieval. Implementations must be deterministic."""

    def index(self, cards: list[EvidenceCard]) -> None: ...

    def query(self, text: str, k: int = 10) -> list[ScoredCard]: ...


class LexicalRetrievalIndex:
    """TF-IDF cosine retrieval over evidence card text. Offline, deterministic."""

    def __init__(self) -> None:
        self._cards: list[EvidenceCard] = []
        self._tf: list[Counter[str]] = []
        self._idf: dict[str, float] = {}

    def index(self, cards: list[EvidenceCard]) -> None:
        self._cards = list(cards)
        self._tf = [Counter(_tokens(_card_text(c))) for c in self._cards]
        n = len(self._tf)
        df: Counter[str] = Counter()
        for counts in self._tf:
            df.update(counts.keys())
        self._idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}

    def _vector(self, counts: Counter[str]) -> dict[str, float]:
        total = sum(counts.values()) or 1
        return {t: (c / total) * self._idf.get(t, 0.0) for t, c in counts.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(v * b.get(t, 0.0) for t, v in a.items())
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def query(self, text: str, k: int = 10) -> list[ScoredCard]:
        qvec = self._vector(Counter(_tokens(text)))
        scored = [
            ScoredCard(
                evidence_id=card.evidence_id,
                score=round(self._cosine(qvec, self._vector(counts)), 6),
                card=card,
            )
            for card, counts in zip(self._cards, self._tf, strict=True)
        ]
        # deterministic order: score desc, then evidence_id asc
        scored.sort(key=lambda s: (-s.score, s.evidence_id))
        return [s for s in scored[:k] if s.score > 0.0]


def build_retrieval_index(backend: str = "lexical") -> RetrievalIndex:
    """Factory. Only the local lexical backend ships; anything else fails loudly."""
    if backend == "lexical":
        return LexicalRetrievalIndex()
    raise ValueError(
        f"unknown retrieval backend {backend!r} — no embedding provider is "
        "configured; only the deterministic local 'lexical' backend exists"
    )
