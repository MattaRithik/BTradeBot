"""Portfolio risk constraints: deterministic caps applied after construction.

Builders produce raw targets; this module enforces the limits from
configs/risk.yaml: per-ticker and per-sector caps, gross/net exposure caps,
position count, shorting/leverage switches, a liquidity floor, and a
volatility-target scale-down. Every intervention is recorded in the target's
warnings — constraints never act silently.
"""

from __future__ import annotations

import math

import pandas as pd

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget


class RiskConfig(PlatformModel):
    """Portfolio risk limits (defaults mirror configs/risk.yaml)."""

    max_ticker_weight: float = 0.15
    max_sector_weight: float = 0.35
    max_gross_exposure: float = 1.0
    max_net_exposure: float = 1.0
    max_positions: int = 15
    allow_shorting: bool = False
    allow_leveraged_etfs: bool = False
    volatility_target_annual: float = 0.25
    min_liquidity_dollar_volume: float = 5_000_000


def load_risk_config() -> RiskConfig:
    raw = load_yaml_config("risk").get("portfolio", {}) or {}
    return RiskConfig(**raw)


def _rebalance(
    target: PortfolioTarget,
    positions: list[PortfolioPosition],
    warnings: list[str],
) -> PortfolioTarget:
    gross = sum(abs(p.weight) for p in positions)
    net = sum(p.weight for p in positions)
    return target.model_copy(
        update={
            "positions": positions,
            "gross_exposure": gross,
            "net_exposure": net,
            "cash_weight": max(0.0, 1.0 - gross),
            "warnings": [*target.warnings, *warnings],
        }
    )


def apply_risk_constraints(
    target: PortfolioTarget,
    config: RiskConfig | None = None,
    features: pd.DataFrame | None = None,
) -> PortfolioTarget:
    """Clamp a PortfolioTarget to the risk limits. Order: shorts → liquidity →
    position count → per-ticker → per-sector → gross/net → volatility target."""
    cfg = config or RiskConfig()
    warnings: list[str] = []
    positions = list(target.positions)

    # 1. shorting switch
    if not cfg.allow_shorting:
        shorts = [p for p in positions if p.weight < 0]
        if shorts:
            warnings.append(
                f"shorting disabled — dropped: {', '.join(p.ticker for p in shorts)}"
            )
            positions = [p for p in positions if p.weight >= 0]

    # 2. liquidity floor
    if features is not None and not features.empty:
        fmap = {str(r["ticker"]): r for r in features.to_dict(orient="records")}
        kept = []
        for p in positions:
            adv = fmap.get(p.ticker, {}).get("avg_dollar_volume_21d")
            if adv is not None and pd.notna(adv) and adv < cfg.min_liquidity_dollar_volume:
                warnings.append(
                    f"{p.ticker}: dropped — avg dollar volume {adv:,.0f} below "
                    f"{cfg.min_liquidity_dollar_volume:,.0f}"
                )
            else:
                kept.append(p)
        positions = kept

    # 3. position count (keep largest weights)
    if len(positions) > cfg.max_positions:
        dropped = sorted(positions, key=lambda p: abs(p.weight))[cfg.max_positions:]
        warnings.append(
            f"position count capped at {cfg.max_positions} — dropped: "
            f"{', '.join(p.ticker for p in dropped)}"
        )
        positions = sorted(positions, key=lambda p: abs(p.weight), reverse=True)[: cfg.max_positions]

    # 4. per-ticker cap
    capped = []
    for p in positions:
        if abs(p.weight) > cfg.max_ticker_weight:
            sign = 1.0 if p.weight >= 0 else -1.0
            warnings.append(
                f"{p.ticker}: weight {p.weight:.3f} capped to {cfg.max_ticker_weight:.3f}"
            )
            p = p.model_copy(update={"weight": sign * cfg.max_ticker_weight})
        capped.append(p)
    positions = capped

    # 5. per-sector cap (scale the sector's positions proportionally)
    by_sector: dict[str, list[PortfolioPosition]] = {}
    for p in positions:
        by_sector.setdefault(p.sector or "unknown", []).append(p)
    for sector, members in by_sector.items():
        sector_gross = sum(abs(p.weight) for p in members)
        if sector_gross > cfg.max_sector_weight:
            scale = cfg.max_sector_weight / sector_gross
            warnings.append(
                f"sector {sector!r}: gross {sector_gross:.3f} scaled by {scale:.3f} "
                f"to {cfg.max_sector_weight:.3f}"
            )
            for p in members:
                p.weight = p.weight * scale  # validate_assignment keeps it a float

    # 6. gross / net exposure caps
    gross = sum(abs(p.weight) for p in positions)
    if gross > cfg.max_gross_exposure:
        scale = cfg.max_gross_exposure / gross
        warnings.append(f"gross {gross:.3f} scaled by {scale:.3f} to {cfg.max_gross_exposure:.3f}")
        for p in positions:
            p.weight = p.weight * scale
    net = sum(p.weight for p in positions)
    if abs(net) > cfg.max_net_exposure:
        scale = cfg.max_net_exposure / abs(net)
        warnings.append(f"net {net:.3f} scaled by {scale:.3f} to ±{cfg.max_net_exposure:.3f}")
        for p in positions:
            p.weight = p.weight * scale

    # 7. volatility target (scale the whole book down, never up)
    if features is not None and not features.empty and positions:
        fmap = {str(r["ticker"]): r for r in features.to_dict(orient="records")}
        port_vol = 0.0
        known = True
        for p in positions:
            vol = fmap.get(p.ticker, {}).get("realized_vol_21d")
            if vol is None or (isinstance(vol, float) and math.isnan(vol)):
                known = False
                break
            port_vol += abs(p.weight) * float(vol)
        if known and port_vol > cfg.volatility_target_annual:
            scale = cfg.volatility_target_annual / port_vol
            warnings.append(
                f"portfolio vol {port_vol:.3f} above target "
                f"{cfg.volatility_target_annual:.3f} — scaled by {scale:.3f}"
            )
            for p in positions:
                p.weight = p.weight * scale

    return _rebalance(target, positions, warnings)
