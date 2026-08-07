"""Strategy builders: signals (+ features) → PortfolioTarget. Python math only.

Every builder takes the run's actionable signals and the feature frame and
produces a PortfolioTarget whose position weights plus cash_weight sum to 1
(gross <= 1 unless a builder explicitly warns about shorting/leverage, which
the schema requires). Risk limits are NOT applied here — that is
portfolio/risk.py's job, so builders stay simple and comparable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pandas as pd

from quant_platform.core.enums import SignalClass, TargetType
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget, Signal

_LONG_CLASSES = {SignalClass.STRONG_LONG, SignalClass.MODERATE_LONG}


def _long_signals(signals: list[Signal]) -> list[Signal]:
    """Actionable long security signals, best score first, deduped by ticker."""
    seen: set[str] = set()
    out = []
    for s in sorted(signals, key=lambda s: s.raw_score, reverse=True):
        if s.action_allowed and s.signal_class in _LONG_CLASSES and s.ticker not in seen:
            seen.add(s.ticker or "")
            out.append(s)
    return out


def _features_map(features: pd.DataFrame | None) -> dict[str, dict]:
    if features is None or features.empty:
        return {}
    return {str(r["ticker"]): r for r in features.to_dict(orient="records")}


def _target(
    strategy: str,
    run_id: str,
    as_of: date,
    weights: dict[str, tuple[float, str, str]],
    warnings: list[str] | None = None,
) -> PortfolioTarget:
    """weights: ticker -> (signed weight, sector, rationale)."""
    positions = [
        PortfolioPosition(
            ticker=t, weight=w, sector=sector, rationale=why,
        )
        for t, (w, sector, why) in sorted(weights.items())
        if abs(w) > 1e-12
    ]
    gross = sum(abs(p.weight) for p in positions)
    net = sum(p.weight for p in positions)
    return PortfolioTarget(
        target_id=stable_id("tgt", run_id, strategy, as_of.isoformat()),
        run_id=run_id,
        strategy=strategy,
        as_of_date=as_of,
        positions=positions,
        cash_weight=max(0.0, 1.0 - gross),
        gross_exposure=gross,
        net_exposure=net,
        warnings=warnings or [],
    )


# -- the eight builders -----------------------------------------------------


def build_long_basket(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
    max_positions: int = 10,
) -> PortfolioTarget:
    """Equal-weight basket of the best actionable security signals."""
    longs = _long_signals(signals)[:max_positions]
    if not longs:
        return build_cash(signals, features, run_id, as_of)
    w = 1.0 / len(longs)
    return _target(
        "long_basket", run_id, as_of,
        {s.ticker: (w, s.sector, f"equal weight, score {s.raw_score:.2f}") for s in longs},
    )


def build_score_weighted(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
    max_positions: int = 10,
) -> PortfolioTarget:
    """Weights proportional to composite score."""
    longs = _long_signals(signals)[:max_positions]
    if not longs:
        return build_cash(signals, features, run_id, as_of)
    total = sum(s.raw_score for s in longs)
    if total <= 0:
        return build_long_basket(signals, features, run_id, as_of, max_positions)
    return _target(
        "score_weighted", run_id, as_of,
        {s.ticker: (s.raw_score / total, s.sector, f"score-weighted {s.raw_score:.2f}") for s in longs},
    )


def build_etf_rotation(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
) -> PortfolioTarget:
    """Concentrate in the ETF of the single best-scoring selected sector."""
    etfs = [s for s in signals
            if s.action_allowed and s.target_type == TargetType.ETF
            and s.signal_class in _LONG_CLASSES]
    if not etfs:
        return build_cash(signals, features, run_id, as_of)
    best = max(etfs, key=lambda s: s.raw_score)
    return _target(
        "etf_rotation", run_id, as_of,
        {best.ticker: (1.0, best.sector, f"top sector ETF, score {best.raw_score:.2f}")},
    )


def build_long_short(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
    max_positions: int = 10,
) -> PortfolioTarget:
    """Long the strong signals, short the AVOID security signals (flagged)."""
    longs = _long_signals(signals)[:max_positions]
    shorts = [s for s in signals
              if s.action_allowed and s.signal_class in
              (SignalClass.AVOID, SignalClass.SHORT_CANDIDATE)][:max_positions]
    if not longs and not shorts:
        return build_cash(signals, features, run_id, as_of)
    weights: dict[str, tuple[float, str, str]] = {}
    if longs:
        w = 0.5 / len(longs)
        weights.update({s.ticker: (w, s.sector, "long leg") for s in longs})
    if shorts:
        w = 0.5 / len(shorts)
        weights.update({s.ticker: (-w, s.sector, "short leg (AVOID signal)") for s in shorts})
    warnings = ["short"] if shorts else []
    return _target("long_short", run_id, as_of, weights, warnings)


def build_momentum(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
    max_positions: int = 10,
) -> PortfolioTarget:
    """Weights proportional to 63d cross-sectional momentum rank."""
    fmap = _features_map(features)
    longs = [s for s in _long_signals(signals)[:max_positions]
             if pd.notna(fmap.get(s.ticker, {}).get("rank_ret_63d", float("nan")))]
    if not longs:
        return build_cash(signals, features, run_id, as_of)
    ranks = {s.ticker: float(fmap[s.ticker]["rank_ret_63d"]) for s in longs}
    total = sum(ranks.values())
    if total <= 0:
        return build_long_basket(signals, features, run_id, as_of, max_positions)
    return _target(
        "momentum", run_id, as_of,
        {s.ticker: (ranks[s.ticker] / total, s.sector, f"momentum rank {ranks[s.ticker]:.2f}")
         for s in longs},
    )


def build_risk_parity(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
    max_positions: int = 10,
) -> PortfolioTarget:
    """Inverse-volatility weights (realized_vol_21d)."""
    fmap = _features_map(features)
    longs = []
    for s in _long_signals(signals)[:max_positions]:
        vol = fmap.get(s.ticker, {}).get("realized_vol_21d", float("nan"))
        if pd.notna(vol) and vol > 0:
            longs.append((s, float(vol)))
    if not longs:
        return build_cash(signals, features, run_id, as_of)
    inv = {s.ticker: 1.0 / vol for s, vol in longs}
    total = sum(inv.values())
    return _target(
        "risk_parity", run_id, as_of,
        {s.ticker: (inv[s.ticker] / total, s.sector, f"inverse vol {vol:.2f}") for s, vol in longs},
    )


def build_ensemble(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
) -> PortfolioTarget:
    """Average of the long-only weight schemes (basket/score/momentum/parity)."""
    parts = [
        build_long_basket(signals, features, run_id, as_of),
        build_score_weighted(signals, features, run_id, as_of),
        build_momentum(signals, features, run_id, as_of),
        build_risk_parity(signals, features, run_id, as_of),
    ]
    combined: dict[str, list[float]] = {}
    sectors: dict[str, str] = {}
    for part in parts:
        for p in part.positions:
            combined.setdefault(p.ticker, []).append(p.weight)
            sectors[p.ticker] = p.sector
    if not combined:
        return build_cash(signals, features, run_id, as_of)
    weights = {
        t: (sum(ws) / len(parts), sectors[t], "ensemble mean of 4 long-only builders")
        for t, ws in combined.items()
    }
    return _target("ensemble", run_id, as_of, weights)


def build_cash(
    signals: list[Signal], features: pd.DataFrame | None, run_id: str, as_of: date,
) -> PortfolioTarget:
    """100% cash — always a valid, explicit portfolio."""
    return _target("cash", run_id, as_of, {}, ["100% cash — no actionable exposure"])


BUILDERS: dict[str, Callable[..., PortfolioTarget]] = {
    "long_basket": build_long_basket,
    "score_weighted": build_score_weighted,
    "etf_rotation": build_etf_rotation,
    "long_short": build_long_short,
    "momentum": build_momentum,
    "risk_parity": build_risk_parity,
    "ensemble": build_ensemble,
    "cash": build_cash,
}


def build_strategy(
    name: str, signals: list[Signal], features: pd.DataFrame | None,
    run_id: str, as_of: date,
) -> PortfolioTarget:
    try:
        builder = BUILDERS[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(BUILDERS)}") from None
    return builder(signals, features, run_id, as_of)
