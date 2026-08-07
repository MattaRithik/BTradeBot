"""Point-in-time repositories: the ONLY research-side path to data.

Every query goes through a fresh TimeGatekeeper bound to the caller's
ResearchContext, so post-cutoff records can never reach research code through
this class. Rejections are audited by the gatekeeper
(DATA_REJECTED_FUTURE); each fetch itself is audited (DATA_FETCH).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from quant_platform.core.audit import AuditLogger
from quant_platform.core.enums import AuditEventType
from quant_platform.core.gatekeeper import ResearchContext, TimeGatekeeper
from quant_platform.core.schemas import FundamentalRecord, MarketBar, NewsRecord
from quant_platform.core.store import ArtifactStore
from quant_platform.data.providers import (
    FundamentalDataProvider,
    MarketDataProvider,
    NewsDataProvider,
    ReferenceDataProvider,
)


class PITRepository:
    """Gatekeeper-enforcing facade over data providers.

    Wraps a MarketDataProvider plus optional news/fundamental providers, an
    ArtifactStore for per-run caching, and an AuditLogger. It is impossible to
    obtain post-cutoff data from this class: results are filtered through a
    TimeGatekeeper created fresh for every call.
    """

    def __init__(
        self,
        market_provider: MarketDataProvider,
        store: ArtifactStore | None = None,
        audit: AuditLogger | None = None,
        news_provider: NewsDataProvider | None = None,
        fundamental_provider: FundamentalDataProvider | ReferenceDataProvider | None = None,
    ) -> None:
        self.market_provider = market_provider
        self.news_provider = news_provider
        self.fundamental_provider = fundamental_provider
        self.store = store
        self.audit = audit

    # -- internals ---------------------------------------------------------
    def _gatekeeper(self, context: ResearchContext) -> TimeGatekeeper:
        return TimeGatekeeper(context=context, audit=self.audit)

    def _record_fetch(self, context: ResearchContext, kind: str, **details: Any) -> None:
        if self.audit is None:
            return
        self.audit.record(
            AuditEventType.DATA_FETCH,
            run_id=context.run_id,
            as_of_date=context.as_of_date.isoformat(),
            kind=kind,
            **details,
        )

    # -- queries (all gatekeeper-filtered) ---------------------------------
    def get_bars(
        self,
        context: ResearchContext,
        tickers: list[str],
        start: date,
        end: date,
    ) -> list[MarketBar]:
        """Daily bars, filtered to timestamps at/before the context cutoff."""
        gate = self._gatekeeper(context)
        fetched = self.market_provider.get_history(tickers, start, end)
        bars = gate.filter_by_timestamp(fetched, what="market_bar")
        self._record_fetch(
            context,
            "bars",
            provider=getattr(self.market_provider, "name", "unknown"),
            tickers=list(tickers),
            start=start.isoformat(),
            end=end.isoformat(),
            fetched=len(fetched),
            returned=len(bars),
            rejected=gate.rejected_count,
        )
        return bars

    def get_news(
        self,
        context: ResearchContext,
        tickers: list[str],
        start: date,
        end: date,
    ) -> list[NewsRecord]:
        """News items, filtered to ``usable_from`` at/before the cutoff."""
        if self.news_provider is None:
            raise ValueError("no news provider configured for this repository")
        gate = self._gatekeeper(context)
        fetched = self.news_provider.get_news(tickers, start, end)
        news = gate.filter_by_usable_from(fetched, what="news_record")
        self._record_fetch(
            context,
            "news",
            provider=getattr(self.news_provider, "name", "unknown"),
            tickers=list(tickers),
            start=start.isoformat(),
            end=end.isoformat(),
            fetched=len(fetched),
            returned=len(news),
            rejected=gate.rejected_count,
        )
        return news

    def get_fundamentals(
        self,
        context: ResearchContext,
        tickers: list[str],
        metrics: list[str],
    ) -> list[FundamentalRecord]:
        """Fundamental/reference records, filtered by ``usable_from``.

        Uses the time-series fundamental provider when available, falling back
        to a reference-style provider (point-in-time snapshot fields).
        """
        if self.fundamental_provider is None:
            raise ValueError("no fundamental provider configured for this repository")
        gate = self._gatekeeper(context)
        provider = self.fundamental_provider
        if isinstance(provider, FundamentalDataProvider):
            fetched = provider.get_fundamentals(tickers, metrics, context.visible_start, context.visible_end)
        elif isinstance(provider, ReferenceDataProvider):
            fetched = provider.get_reference(tickers, metrics)
        else:
            raise ValueError(f"unsupported fundamental provider: {provider!r}")
        records = gate.filter_by_usable_from(fetched, what="fundamental_record")
        self._record_fetch(
            context,
            "fundamentals",
            provider=getattr(provider, "name", "unknown"),
            tickers=list(tickers),
            metrics=list(metrics),
            fetched=len(fetched),
            returned=len(records),
            rejected=gate.rejected_count,
        )
        return records

    # -- caching ------------------------------------------------------------
    def cache_frame(
        self,
        context: ResearchContext,
        df: pd.DataFrame,
        sub: str = "normalized",
        name: str | None = None,
    ) -> Any:
        """Persist a tidy frame for this run (e.g. normalized bars as parquet)."""
        if self.store is None:
            raise ValueError("no ArtifactStore configured for this repository")
        artifact_name = name or f"bars_{context.run_id}"
        path = self.store.save_table(sub, artifact_name, df)
        self._record_fetch(
            context,
            "cache_frame",
            artifact=str(path),
            rows=len(df),
        )
        return path
