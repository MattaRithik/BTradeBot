"""Deterministic, point-in-time feature engine.

Pure pandas/numpy: no I/O, no randomness, no hidden state. Given a tidy bar
frame and an ``as_of`` date it produces one row of features per ticker using
ONLY observations with ``timestamp <= end-of-day(as_of)`` (UTC) — the cutoff
filter is applied defensively inside this function, so feeding post-``as_of``
rows never changes the output.

Trading-day conventions (documented assumptions):
    21d  ≈ 1 trading month, 63d ≈ 1 trading quarter, 126d ≈ 6 months,
    252 trading days per year (used to annualize realized volatility).

Short histories never crash the engine: any feature lacking enough
observations is NaN.

Input frame columns: ticker, timestamp (UTC), open, high, low, close, volume.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from quant_platform.core.timeutil import end_of_day_utc

TRADING_DAYS_PER_YEAR = 252

FEATURE_COLUMNS = [
    "ticker",
    "last_close",
    "ret_1d",
    "ret_5d",
    "ret_21d",
    "ret_63d",
    "ret_126d",
    "realized_vol_21d",
    "avg_volume_21d",
    "avg_dollar_volume_21d",
    "rel_strength_63d",
    "benchmark_rel_ret_21d",
    "drawdown",
    "ma_dist_20d",
    "ma_dist_50d",
    "ma_dist_200d",
    "vol_regime",
    "rank_ret_21d",
    "rank_ret_63d",
    "rank_dollar_volume",
    "sector_rel_strength_63d",
]


def _ret(close: pd.Series, n: int) -> float:
    """n-trading-day simple return; NaN when history is too short."""
    if len(close) <= n:
        return float("nan")
    base = close.iloc[-1 - n]
    if base <= 0:
        return float("nan")
    return float(close.iloc[-1] / base - 1.0)


def _ma_dist(close: pd.Series, n: int) -> float:
    """(close - MA_n) / MA_n; NaN when fewer than n observations exist."""
    if len(close) < n:
        return float("nan")
    ma = close.tail(n).mean()
    if ma <= 0:
        return float("nan")
    return float(close.iloc[-1] / ma - 1.0)


def _per_ticker_features(group: pd.DataFrame) -> dict[str, float]:
    g = group.sort_values("timestamp")
    close = g["close"].astype(float).reset_index(drop=True)
    volume = g["volume"].astype(float).reset_index(drop=True)
    rets = close.pct_change()

    realized_vol_21d = float("nan")
    tail_rets = rets.tail(21).dropna()
    if len(tail_rets) >= 2:
        realized_vol_21d = float(tail_rets.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))

    rolling_vol = rets.rolling(21).std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)
    vol_median = rolling_vol.tail(126).median()
    vol_regime = float("nan")
    if pd.notna(vol_median) and vol_median > 0 and pd.notna(realized_vol_21d):
        vol_regime = float(realized_vol_21d / vol_median)

    dollar_volume = (close * volume).tail(21)
    running_max = close.cummax()

    return {
        "last_close": float(close.iloc[-1]),
        "ret_1d": _ret(close, 1),
        "ret_5d": _ret(close, 5),
        "ret_21d": _ret(close, 21),
        "ret_63d": _ret(close, 63),
        "ret_126d": _ret(close, 126),
        "realized_vol_21d": realized_vol_21d,
        "avg_volume_21d": float(volume.tail(21).mean()),
        "avg_dollar_volume_21d": float(dollar_volume.mean()),
        "drawdown": float(close.iloc[-1] / running_max.iloc[-1] - 1.0),
        "ma_dist_20d": _ma_dist(close, 20),
        "ma_dist_50d": _ma_dist(close, 50),
        "ma_dist_200d": _ma_dist(close, 200),
        "vol_regime": vol_regime,
    }


def compute_features(
    df: pd.DataFrame,
    as_of: date,
    benchmark: str = "SPY",
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    """One row of point-in-time features per ticker.

    Only rows with ``timestamp <= end_of_day_utc(as_of)`` are used. ``benchmark``
    names a ticker inside ``df`` used for relative-strength features; when it is
    absent those features are NaN. ``sector_map`` (ticker -> sector label)
    enables ``sector_rel_strength_63d``; without it the column is all-NaN.
    """
    if df.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS)

    cutoff = pd.Timestamp(end_of_day_utc(as_of))
    work = df.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    work = work[work["timestamp"] <= cutoff]  # defensive anti-lookahead filter
    if work.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS)

    rows: dict[str, dict[str, float]] = {}
    for ticker, group in work.groupby("ticker"):
        rows[str(ticker)] = _per_ticker_features(group)

    bench = rows.get(benchmark)
    bench_ret_21d = bench["ret_21d"] if bench else float("nan")
    bench_ret_63d = bench["ret_63d"] if bench else float("nan")

    records = []
    for ticker, feats in rows.items():
        rec = {"ticker": ticker, **feats}
        rec["rel_strength_63d"] = (
            feats["ret_63d"] - bench_ret_63d
            if pd.notna(feats["ret_63d"]) and pd.notna(bench_ret_63d)
            else float("nan")
        )
        rec["benchmark_rel_ret_21d"] = (
            feats["ret_21d"] - bench_ret_21d
            if pd.notna(feats["ret_21d"]) and pd.notna(bench_ret_21d)
            else float("nan")
        )
        records.append(rec)

    out = pd.DataFrame.from_records(records)

    # cross-sectional percentile ranks in [0, 1] (NaN stays NaN)
    out["rank_ret_21d"] = out["ret_21d"].rank(pct=True)
    out["rank_ret_63d"] = out["ret_63d"].rank(pct=True)
    out["rank_dollar_volume"] = out["avg_dollar_volume_21d"].rank(pct=True)

    if sector_map is not None:
        sectors = out["ticker"].map(sector_map)
        sector_mean = out["ret_63d"].groupby(sectors).transform("mean")
        has_sector = sectors.notna() & out["ret_63d"].notna()
        out["sector_rel_strength_63d"] = np.where(
            has_sector, out["ret_63d"] - sector_mean, np.nan
        )
    else:
        out["sector_rel_strength_63d"] = np.nan

    return out[FEATURE_COLUMNS].reset_index(drop=True)
