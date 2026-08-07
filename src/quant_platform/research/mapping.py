"""Security mapping + tradability filters. Pure Python, no LLM involvement.

A thesis names a sector (a label); this module maps it to concrete securities
from configs/universe.yaml and decides — with deterministic rules only —
which of them are tradable at all (history length, price floor, liquidity).
Non-tradable candidates are kept in the result with explicit reasons; they
are never silently dropped and never silently passed.
"""

from __future__ import annotations

from datetime import date

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import ExposureType, PlatformModel
from quant_platform.core.schemas import (
    CompanyMapping,
    ETFMapping,
    MarketBar,
    TradabilityResult,
)


class TradabilityFilters(PlatformModel):
    """Tradability thresholds (defaults mirror configs/universe.yaml)."""

    min_history_days: int = 126
    min_price: float = 2.0
    min_avg_dollar_volume: float = 5_000_000
    min_volume: int = 1
    dollar_volume_window: int = 21


def load_tradability_filters() -> TradabilityFilters:
    raw = load_yaml_config("universe").get("tradability", {}) or {}
    return TradabilityFilters(**raw)


def load_universe() -> dict[str, dict[str, list[str]]]:
    return load_yaml_config("universe").get("universe", {}) or {}


def check_tradability(
    ticker: str,
    bars: list[MarketBar],
    as_of_date: date,
    filters: TradabilityFilters | None = None,
) -> TradabilityResult:
    """Apply deterministic tradability rules to one ticker's bar history."""
    f = filters or TradabilityFilters()
    ordered = sorted(bars, key=lambda b: b.timestamp)
    reasons: list[str] = []

    history_days = len(ordered)
    if history_days < f.min_history_days:
        reasons.append(f"insufficient history: {history_days}d < {f.min_history_days}d")

    last_price = float(ordered[-1].close) if ordered else None
    if last_price is None:
        reasons.append("no bars at all")
    elif last_price < f.min_price:
        reasons.append(f"last price {last_price:.2f} below floor {f.min_price:.2f}")

    window = ordered[-f.dollar_volume_window :] if ordered else []
    avg_dollar_volume = None
    if window:
        avg_dollar_volume = sum(float(b.close) * float(b.volume) for b in window) / len(window)
        if avg_dollar_volume < f.min_avg_dollar_volume:
            reasons.append(
                f"avg dollar volume {avg_dollar_volume:,.0f} below minimum "
                f"{f.min_avg_dollar_volume:,.0f}"
            )
        if sum(1 for b in window if b.volume < f.min_volume):
            reasons.append(f"zero/sub-min volume days inside the {f.dollar_volume_window}d window")

    return TradabilityResult(
        ticker=ticker,
        tradable=not reasons,
        reasons=reasons,
        avg_dollar_volume=avg_dollar_volume,
        history_days=history_days,
        last_price=last_price,
        as_of_date=as_of_date,
    )


def map_sector_securities(
    sector_id: str,
    sector_label: str,
    as_of_date: date,
    universe: dict[str, dict[str, list[str]]] | None = None,
    evidence_tickers: set[str] | None = None,
) -> list[CompanyMapping]:
    """Map a sector to its configured candidate securities.

    Exposure is DIRECT when the ticker appears in the sector's evidence,
    WATCHLIST otherwise (configured candidate, not yet evidence-backed).
    """
    uni = universe if universe is not None else load_universe()
    entry = uni.get(sector_id, {})
    seen = set(evidence_tickers or set())
    mappings = []
    for ticker in entry.get("securities", []):
        direct = ticker in seen
        mappings.append(
            CompanyMapping(
                sector=sector_label,
                ticker=ticker,
                exposure=ExposureType.DIRECT if direct else ExposureType.WATCHLIST,
                exposure_rationale=(
                    "named in sector evidence" if direct else "configured candidate, no direct evidence yet"
                ),
                as_of_date=as_of_date,
            )
        )
    return mappings


def map_sector_etfs(
    sector_id: str,
    sector_label: str,
    as_of_date: date,
    universe: dict[str, dict[str, list[str]]] | None = None,
) -> list[ETFMapping]:
    """Map a sector to its configured ETFs (always INDIRECT exposure)."""
    uni = universe if universe is not None else load_universe()
    entry = uni.get(sector_id, {})
    return [
        ETFMapping(sector=sector_label, etf_ticker=t, as_of_date=as_of_date)
        for t in entry.get("etfs", [])
    ]
