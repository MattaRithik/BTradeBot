"""Shared fixtures: synthetic data builders and isolated data roots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import Direction, EvidenceCategory, SourceType
from quant_platform.core.gatekeeper import ResearchContext, TimeGatekeeper
from quant_platform.core.schemas import EvidenceCard, MarketBar, NewsRecord
from quant_platform.core.store import ArtifactStore
from quant_platform.core.timeutil import end_of_day_utc

AS_OF = date(2024, 12, 31)
VISIBLE_START = date(2023, 1, 1)


def dt(y: int, m: int, d: int, hh: int = 12) -> datetime:
    return datetime(y, m, d, hh, tzinfo=UTC)


def make_bar(
    ticker: str = "NVDA",
    ts: datetime | None = None,
    close: float = 100.0,
    source: SourceType = SourceType.SYNTHETIC,
) -> MarketBar:
    ts = ts or dt(2024, 6, 3)
    return MarketBar(
        ticker=ticker,
        raw_security=f"{ticker} US Equity",
        timestamp=ts,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000,
        source=source,
        retrieved_at=dt(2024, 6, 4),
    )


def make_news(news_id: str = "n1", usable: datetime | None = None) -> NewsRecord:
    usable = usable or dt(2024, 6, 3)
    return NewsRecord(
        news_id=news_id,
        source=SourceType.SYNTHETIC,
        headline="synthetic headline",
        securities=["NVDA"],
        published_at=usable,
        usable_from=usable,
        retrieved_at=usable,
    )


def make_evidence(evidence_id: str = "ev1", usable: datetime | None = None) -> EvidenceCard:
    usable = usable or dt(2024, 6, 3)
    return EvidenceCard(
        evidence_id=evidence_id,
        source=SourceType.SYNTHETIC,
        published_at=usable,
        usable_from=usable,
        securities=["NVDA"],
        sectors=["AI Infrastructure"],
        category=EvidenceCategory.DEMAND_SIGNAL,
        direction=Direction.POSITIVE,
        confidence=0.8,
        relevance=0.9,
        claim="synthetic claim",
    )


@pytest.fixture()
def context() -> ResearchContext:
    return ResearchContext(
        run_id="test_run",
        as_of_date=AS_OF,
        visible_start=VISIBLE_START,
        visible_end=AS_OF,
        test_start=date(2025, 1, 1),
        test_end=date(2025, 2, 28),
    )


@pytest.fixture()
def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "logs" / "audit.jsonl")


@pytest.fixture()
def gatekeeper(context: ResearchContext, audit: AuditLogger) -> TimeGatekeeper:
    return TimeGatekeeper(context=context, audit=audit)


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "data")


CUTOFF = end_of_day_utc(AS_OF)
