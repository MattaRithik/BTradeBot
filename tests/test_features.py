"""Feature engine: hand-computable values and feature-level anti-lookahead."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from quant_platform.features import compute_features

N_DAYS = 150
AS_OF = date(2024, 6, 28)  # a Friday; end of the synthetic series


def _frame(n_days: int = N_DAYS, end: str = "2024-06-28") -> pd.DataFrame:
    """AAA rises linearly (close = 100 + i); SPY is flat at 100."""
    dates = pd.bdate_range(end=end, periods=n_days, tz="UTC")
    rows = []
    for i, ts in enumerate(dates):
        rows.append({"ticker": "AAA", "timestamp": ts, "open": 99 + i, "high": 101 + i,
                     "low": 98 + i, "close": 100.0 + i, "volume": 1000.0})
        rows.append({"ticker": "SPY", "timestamp": ts, "open": 100.0, "high": 100.0,
                     "low": 100.0, "close": 100.0, "volume": 2000.0})
    return pd.DataFrame(rows)


def _row(out: pd.DataFrame, ticker: str) -> pd.Series:
    return out.set_index("ticker").loc[ticker]


def test_returns_hand_computed() -> None:
    out = compute_features(_frame(), AS_OF, benchmark="SPY")
    aaa = _row(out, "AAA")
    last = 100.0 + (N_DAYS - 1)  # 249
    assert aaa["ret_5d"] == pytest.approx(last / (last - 5) - 1.0)
    assert aaa["ret_21d"] == pytest.approx(last / (last - 21) - 1.0)
    assert aaa["ret_63d"] == pytest.approx(last / (last - 63) - 1.0)
    assert aaa["ret_126d"] == pytest.approx(last / (last - 126) - 1.0)
    assert aaa["ret_1d"] == pytest.approx(last / (last - 1) - 1.0)


def test_avg_dollar_volume() -> None:
    out = compute_features(_frame(), AS_OF, benchmark="SPY")
    aaa = _row(out, "AAA")
    closes = [100.0 + i for i in range(N_DAYS - 21, N_DAYS)]
    assert aaa["avg_volume_21d"] == pytest.approx(1000.0)
    assert aaa["avg_dollar_volume_21d"] == pytest.approx(sum(closes) / 21 * 1000.0)


def test_monotonic_series_has_zero_drawdown() -> None:
    out = compute_features(_frame(), AS_OF, benchmark="SPY")
    assert _row(out, "AAA")["drawdown"] == pytest.approx(0.0)


def test_relative_strength_sign_and_flat_benchmark() -> None:
    out = compute_features(_frame(), AS_OF, benchmark="SPY")
    aaa, spy = _row(out, "AAA"), _row(out, "SPY")
    assert spy["ret_63d"] == pytest.approx(0.0)  # flat benchmark
    assert aaa["rel_strength_63d"] > 0  # rising ticker beats flat benchmark
    assert aaa["rel_strength_63d"] == pytest.approx(aaa["ret_63d"])
    assert aaa["benchmark_rel_ret_21d"] == pytest.approx(aaa["ret_21d"])
    assert spy["rel_strength_63d"] == pytest.approx(0.0)


def test_realized_vol_and_ma_dist() -> None:
    out = compute_features(_frame(), AS_OF, benchmark="SPY")
    aaa, spy = _row(out, "AAA"), _row(out, "SPY")
    # linear closes: daily simple returns 1/c for c = 228..248 over the last 21
    rets = pd.Series([1.0 / c for c in range(228, 249)])
    assert aaa["realized_vol_21d"] == pytest.approx(rets.std(ddof=1) * math.sqrt(252))
    assert spy["realized_vol_21d"] == pytest.approx(0.0)
    ma20 = sum(100.0 + i for i in range(N_DAYS - 20, N_DAYS)) / 20
    assert aaa["ma_dist_20d"] == pytest.approx((100.0 + N_DAYS - 1) / ma20 - 1.0)


def test_ranks_in_unit_interval() -> None:
    out = compute_features(_frame(), AS_OF, benchmark="SPY")
    for col in ("rank_ret_21d", "rank_ret_63d", "rank_dollar_volume"):
        assert out[col].between(0.0, 1.0).all()
    assert _row(out, "AAA")["rank_ret_21d"] == pytest.approx(1.0)
    assert _row(out, "SPY")["rank_ret_21d"] == pytest.approx(0.5)


def test_sector_relative_strength() -> None:
    sector_map = {"AAA": "Tech", "SPY": "Benchmark"}
    out = compute_features(_frame(), AS_OF, benchmark="SPY", sector_map=sector_map)
    # each ticker is alone in its sector -> relative strength vs sector mean is 0
    assert _row(out, "AAA")["sector_rel_strength_63d"] == pytest.approx(0.0)
    assert _row(out, "SPY")["sector_rel_strength_63d"] == pytest.approx(0.0)
    no_sector = compute_features(_frame(), AS_OF, benchmark="SPY")
    assert no_sector["sector_rel_strength_63d"].isna().all()


def test_short_history_yields_nan_not_crash() -> None:
    out = compute_features(_frame(n_days=10), AS_OF, benchmark="SPY")
    aaa = _row(out, "AAA")
    assert math.isnan(aaa["ret_21d"])
    assert math.isnan(aaa["ret_63d"])
    assert math.isnan(aaa["ma_dist_200d"])
    assert aaa["ret_5d"] == pytest.approx((100.0 + 9) / (100.0 + 4) - 1.0)


def test_post_as_of_rows_do_not_change_results() -> None:
    base = compute_features(_frame(), AS_OF, benchmark="SPY")
    extra_dates = pd.bdate_range(start="2024-07-01", periods=20, tz="UTC")  # after AS_OF
    future_rows = []
    for i, ts in enumerate(extra_dates):
        future_rows.append({"ticker": "AAA", "timestamp": ts, "open": 1.0, "high": 2.0,
                            "low": 1.0, "close": 999.0 + i, "volume": 9e9})
        future_rows.append({"ticker": "SPY", "timestamp": ts, "open": 1.0, "high": 2.0,
                            "low": 1.0, "close": 1.0, "volume": 9e9})
    polluted = compute_features(pd.concat([_frame(), pd.DataFrame(future_rows)]), AS_OF, benchmark="SPY")
    pd.testing.assert_frame_equal(base, polluted)
