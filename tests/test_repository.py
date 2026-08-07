"""PITRepository: post-cutoff data must never reach research code."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from conftest import dt, make_bar, make_news
from quant_platform.core.enums import AuditEventType, SourceType
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import FundamentalRecord
from quant_platform.data.repository import PITRepository

PRE_BAR_TS = dt(2024, 12, 30)
POST_BAR_TS = dt(2025, 1, 15)  # after AS_OF (2024-12-31)
PRE_NEWS_TS = dt(2024, 12, 20)
POST_NEWS_TS = dt(2025, 2, 1)


class StubMarketProvider:
    name = "stub_market"

    def __init__(self, bars: list) -> None:
        self._bars = bars

    def get_history(self, tickers, start, end, fields=None):
        return list(self._bars)


class StubNewsProvider:
    name = "stub_news"

    def __init__(self, news: list) -> None:
        self._news = news

    def get_news(self, tickers, start, end):
        return list(self._news)


class StubFundamentalProvider:
    name = "stub_fundamentals"

    def __init__(self, records: list) -> None:
        self._records = records

    def get_fundamentals(self, tickers, metrics, start, end):
        return list(self._records)

    def diagnose(self):
        return None


def _fund(metric: str, usable: datetime) -> FundamentalRecord:
    return FundamentalRecord(
        ticker="NVDA",
        metric=metric,
        value=1.0,
        published_at=usable,
        usable_from=usable,
        source=SourceType.SYNTHETIC,
        retrieved_at=usable,
    )


def test_get_bars_filters_post_cutoff(context: ResearchContext, audit, store) -> None:
    pre = make_bar(ts=PRE_BAR_TS, close=101.0)
    post = make_bar(ts=POST_BAR_TS, close=150.0)
    repo = PITRepository(StubMarketProvider([pre, post]), store=store, audit=audit)

    bars = repo.get_bars(context, ["NVDA"], date(2024, 12, 1), date(2025, 2, 1))

    assert [b.timestamp for b in bars] == [PRE_BAR_TS]
    assert all(b.timestamp <= context.cutoff_instant for b in bars)
    assert audit.count_by_type(AuditEventType.DATA_FETCH) == 1
    assert audit.count_by_type(AuditEventType.DATA_REJECTED_FUTURE) == 1


def test_get_bars_pre_cutoff_returned_intact(context: ResearchContext, audit) -> None:
    pre = make_bar(ts=PRE_BAR_TS, close=101.0)
    repo = PITRepository(StubMarketProvider([pre]), audit=audit)

    bars = repo.get_bars(context, ["NVDA"], date(2024, 12, 1), date(2024, 12, 31))

    assert len(bars) == 1
    assert bars[0].close == 101.0
    assert bars[0].ticker == "NVDA"
    assert audit.count_by_type(AuditEventType.DATA_REJECTED_FUTURE) == 0


def test_get_news_filters_by_usable_from(context: ResearchContext, audit) -> None:
    pre = make_news(news_id="pre", usable=PRE_NEWS_TS)
    post = make_news(news_id="post", usable=POST_NEWS_TS)
    repo = PITRepository(
        StubMarketProvider([]), audit=audit, news_provider=StubNewsProvider([pre, post])
    )

    news = repo.get_news(context, ["NVDA"], date(2024, 12, 1), date(2025, 2, 1))

    assert [n.news_id for n in news] == ["pre"]
    assert audit.count_by_type(AuditEventType.DATA_FETCH) == 1
    assert audit.count_by_type(AuditEventType.DATA_REJECTED_FUTURE) == 1


def test_get_fundamentals_filters_by_usable_from(context: ResearchContext, audit) -> None:
    pre = _fund("revenue", PRE_NEWS_TS)
    post = _fund("revenue", POST_NEWS_TS)
    repo = PITRepository(
        StubMarketProvider([]), audit=audit, fundamental_provider=StubFundamentalProvider([pre, post])
    )

    records = repo.get_fundamentals(context, ["NVDA"], ["revenue"])

    assert len(records) == 1
    assert records[0].usable_from == PRE_NEWS_TS
    assert audit.count_by_type(AuditEventType.DATA_FETCH) == 1
    assert audit.count_by_type(AuditEventType.DATA_REJECTED_FUTURE) == 1


def test_cache_frame_saves_parquet(context: ResearchContext, audit, store) -> None:
    repo = PITRepository(StubMarketProvider([]), store=store, audit=audit)
    df = pd.DataFrame({"ticker": ["NVDA"], "close": [100.0]})

    path = repo.cache_frame(context, df)

    assert path.exists()
    assert path.parent == store.dir("normalized")
    assert store.load_table("normalized", f"bars_{context.run_id}").equals(df)
