"""Schema invariants: UTC strictness, bounded scores, signal safety rules."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from conftest import dt, make_bar
from quant_platform.core.enums import SignalClass, TargetType
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import MarketBar, ScoreBreakdown, Signal


class TestUtcStrictness:
    def test_naive_datetime_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="naive datetime"):
            make_bar(ts=datetime(2024, 6, 3, 12))  # naive — no tzinfo

    def test_non_utc_coerced_to_utc(self):
        from datetime import timedelta

        tz_plus2 = timezone(timedelta(hours=2))
        bar = make_bar(ts=datetime(2024, 6, 3, 14, tzinfo=tz_plus2))
        assert bar.timestamp.utcoffset().total_seconds() == 0
        assert bar.timestamp.hour == 12


class TestMarketBarValidation:
    def test_impossible_ohlc_rejected(self):
        with pytest.raises(ValueError):
            MarketBar(
                ticker="X",
                timestamp=dt(2024, 6, 3),
                open=100.0,
                high=99.0,  # high < open — impossible
                low=98.0,
                close=99.5,
                volume=10,
                source="synthetic",
                retrieved_at=dt(2024, 6, 4),
            )

    def test_nonpositive_price_rejected(self):
        with pytest.raises(ValueError):
            make_bar(close=0.0)


class TestSignalSafety:
    def _signal(self, **kw):
        base = dict(
            signal_id="s1",
            target="NVDA",
            target_type=TargetType.SECURITY,
            ticker="NVDA",
            raw_score=0.8,
            confidence=0.7,
            signal_class=SignalClass.STRONG_LONG,
            action_allowed=True,
            as_of_date=date(2024, 12, 31),
        )
        base.update(kw)
        return Signal(**base)

    def test_sector_signal_cannot_be_actionable(self):
        with pytest.raises(ValueError, match="action_allowed"):
            self._signal(
                target="AI Infrastructure",
                target_type=TargetType.SECTOR,
                ticker=None,
            )

    def test_sector_signal_never_carries_ticker(self):
        with pytest.raises(ValueError):
            self._signal(
                target="AI Infrastructure",
                target_type=TargetType.SECTOR,
                ticker="NVDA",
                action_allowed=False,
            )

    def test_security_signal_requires_ticker(self):
        with pytest.raises(ValueError, match="ticker"):
            self._signal(ticker=None)

    def test_cash_signal_targets_cash(self):
        sig = self._signal(
            target="CASH",
            target_type=TargetType.CASH,
            ticker=None,
            signal_class=SignalClass.CASH,
            action_allowed=False,
        )
        assert sig.signal_class == SignalClass.CASH

    def test_scores_bounded(self):
        with pytest.raises(ValueError):
            ScoreBreakdown(trend_strength=1.5)


class TestStableIds:
    def test_deterministic(self):
        a = stable_id("thesis", "AI Infrastructure", date(2024, 12, 31))
        b = stable_id("thesis", "AI Infrastructure", date(2024, 12, 31))
        assert a == b and a.startswith("thesis_")

    def test_order_sensitive(self):
        assert stable_id("x", 1, 2) != stable_id("x", 2, 1)
