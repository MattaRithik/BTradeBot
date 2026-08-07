"""Point-in-time market and fundamental data schemas."""

from __future__ import annotations

from datetime import date

from pydantic import Field, model_validator

from quant_platform.core.enums import PlatformModel, SourceType
from quant_platform.core.timeutil import UtcDatetime


class PointInTimeFields(PlatformModel):
    """Provenance mixin: where a datum came from and when it became usable."""

    source: SourceType
    source_ref: str = ""  # raw/source identifier (e.g. Bloomberg request id, file name)
    retrieved_at: UtcDatetime  # when THIS system fetched/imported it


class MarketBar(PointInTimeFields):
    """One OHLCV observation. ``ticker`` is normalized; ``raw_security`` preserves
    the original identifier (e.g. ``NVDA US Equity``)."""

    ticker: str
    raw_security: str = ""
    timestamp: UtcDatetime  # observation time (bar date, typically EOD)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    currency: str = "USD"
    adjusted: bool = True  # adjusted for splits/dividends per source fields
    observed_at: UtcDatetime | None = None  # when the bar became observable

    @model_validator(mode="after")
    def _ohlc_sane(self) -> MarketBar:
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open outside [low, high] for {self.ticker} @ {self.timestamp}")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close outside [low, high] for {self.ticker} @ {self.timestamp}")
        return self


class MarketSnapshot(PointInTimeFields):
    """A point-in-time cross-section of latest known prices."""

    as_of: UtcDatetime
    last_prices: dict[str, float] = Field(default_factory=dict)
    volumes: dict[str, float] = Field(default_factory=dict)


class FundamentalRecord(PointInTimeFields):
    """One fundamental metric observation with point-in-time availability.

    ``published_at`` is when the figure was released; ``usable_from`` is when a
    research agent at time T may first rely on it (>= published_at).
    """

    ticker: str
    metric: str  # e.g. REVENUE, GROSS_MARGIN, CUR_MKT_CAP, PE_RATIO, SHORT_INT
    value: float
    period_end: date | None = None
    published_at: UtcDatetime
    usable_from: UtcDatetime
    unit: str = ""
    revision: int = 0  # restatement counter; later revisions must prove availability
