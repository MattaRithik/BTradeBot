"""News ↔ signal ↔ return attribution. Descriptive statistics ONLY.

Everything here is Python over frozen artifacts. These functions measure
association (accuracy, IC, event-study means, calibration) — they NEVER
support causal claims, and their docstrings say so. Inputs are aligned
(ticker, date) frames; no data fetching happens in this module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from quant_platform.core.enums import Direction, EvidenceCategory
from quant_platform.core.schemas import EvidenceCard

_DIRECTION_SIGN = {
    Direction.POSITIVE: 1.0,
    Direction.NEGATIVE: -1.0,
    Direction.NEUTRAL: 0.0,
    Direction.MIXED: 0.0,
}


def _to_sign(value: object) -> float:
    """Direction enum / its string value / a numeric sign -> -1, 0, +1."""
    try:
        return _DIRECTION_SIGN[Direction(value)]  # handles enum and raw strings
    except (ValueError, KeyError):
        pass
    try:
        return float(np.sign(float(value)))
    except (TypeError, ValueError):
        return 0.0


def directional_accuracy(
    directions: pd.Series, forward_returns: pd.Series
) -> float:
    """Share of non-neutral calls whose sign matched the realized return.

    ``directions``: Direction values (or sign floats); ``forward_returns``:
    aligned realized returns. Neutral/mixed calls are excluded (they make no
    directional claim).
    """
    signs = directions.map(_to_sign)
    mask = (signs != 0) & forward_returns.notna() & (forward_returns != 0)
    if mask.sum() == 0:
        return 0.0
    correct = (signs[mask] * forward_returns[mask]) > 0
    return float(correct.mean())


def information_coefficient(
    scores: pd.Series, forward_returns: pd.Series
) -> dict[str, float]:
    """Pearson and Spearman correlation between scores and realized returns.

    This is rank/linear ASSOCIATION — predictive correlation, never proof of
    causation. NaN pairs are dropped pairwise; < 3 pairs → NaN.
    """
    aligned = pd.concat([scores, forward_returns], axis=1).dropna()
    if len(aligned) < 3:
        return {"pearson": float("nan"), "spearman": float("nan")}
    x, y = aligned.iloc[:, 0], aligned.iloc[:, 1]
    pearson = float(stats.pearsonr(x, y)[0]) if x.std() > 0 and y.std() > 0 else float("nan")
    spearman = (
        float(stats.spearmanr(x, y)[0]) if x.nunique() > 1 and y.nunique() > 1 else float("nan")
    )
    return {"pearson": pearson, "spearman": spearman}


def event_study(
    prices: pd.DataFrame,
    event_dates: pd.Series | list,
    horizons: tuple[int, ...] = (5, 21, 42),
) -> pd.DataFrame:
    """Mean forward return after events, per horizon (in trading days).

    ``prices``: single-ticker close series frame with a UTC datetime index
    and a ``close`` column. ``event_dates``: datetimes of news/events.
    Events without enough forward history contribute to the horizons they
    can reach only. Association around events — not causation.
    """
    close = prices.sort_index()["close"].astype(float)
    rets = close.pct_change()
    rows: dict[int, list[float]] = {h: [] for h in horizons}
    for event in pd.to_datetime(pd.Series(list(event_dates)), utc=True):
        after = rets[rets.index > event]
        for h in horizons:
            window = after.head(h)
            if len(window) >= h:
                rows[h].append(float((1.0 + window).prod() - 1.0))
    return pd.DataFrame(
        {
            "horizon_days": list(horizons),
            "n_events": [len(rows[h]) for h in horizons],
            "mean_forward_return": [float(np.mean(rows[h])) if rows[h] else float("nan")
                                    for h in horizons],
            "median_forward_return": [float(np.median(rows[h])) if rows[h] else float("nan")
                                      for h in horizons],
        }
    )


def category_performance(
    cards: list[EvidenceCard],
    forward_returns: dict[str, float],
) -> pd.DataFrame:
    """Mean realized forward return per evidence category (descriptive)."""
    rows = []
    for category in EvidenceCategory:
        rets = [
            forward_returns[t]
            for c in cards
            if c.category == category
            for t in c.securities
            if t in forward_returns
        ]
        if rets:
            rows.append(
                {
                    "category": category.value,
                    "n": len(rets),
                    "mean_forward_return": float(np.mean(rets)),
                }
            )
    return pd.DataFrame(rows, columns=["category", "n", "mean_forward_return"])


def confidence_calibration(
    confidences: pd.Series, correct: pd.Series, n_buckets: int = 5
) -> pd.DataFrame:
    """Stated confidence vs realized hit rate, bucketed.

    A calibrated system has mean confidence ≈ hit rate per bucket. This
    measures calibration of the LLM's self-reported confidence — association,
    not causation.
    """
    df = pd.DataFrame({"confidence": confidences, "correct": correct.astype(float)}).dropna()
    if df.empty:
        return pd.DataFrame(columns=["bucket", "n", "mean_confidence", "hit_rate"])
    df["bucket"] = pd.cut(df["confidence"], bins=n_buckets, include_lowest=True)
    out = df.groupby("bucket", observed=True).agg(
        n=("correct", "size"),
        mean_confidence=("confidence", "mean"),
        hit_rate=("correct", "mean"),
    )
    return out.reset_index()
