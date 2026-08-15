"""Score component calculators: every component is MEASURED or explicitly missing.

The REAL runtime never injects placeholder constants (no ``0.5``-style
stand-ins). Each calculator returns a float in [0, 1] derived from traceable
inputs — evidence cards, gatekeeper-filtered market features, PIT-safe
fundamentals, or a specialist agent's validated argument — or ``None`` when
the inputs do not exist at this as-of date (missing is explicit; the scoring
layer renormalizes and applies a completeness penalty).
"""

from __future__ import annotations

import pandas as pd

from quant_platform.core.enums import Direction, EvidenceCategory
from quant_platform.core.schemas import AgentArgument, EvidenceCard, SectorThesis


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def trend_strength(thesis: SectorThesis) -> float:
    """Thesis confidence, already evidence-bounded by the thesis builder."""
    return thesis.confidence


def evidence_quality(cards: list[EvidenceCard]) -> float | None:
    """Mean confidence*relevance over the sector's cards; None when no cards."""
    if not cards:
        return None
    return min(1.0, _mean([c.confidence * c.relevance for c in cards]) or 0.0)


def supply_chain_confidence(
    cards: list[EvidenceCard], argument: AgentArgument | None
) -> float | None:
    """Evidence-backed causal-chain coverage + specialist conviction.

    - bottleneck/driver cards present: their mean confidence is the base;
    - the supply_chain specialist's argument (if it ran) contributes its
      confidence, signed by direction (negative direction lowers confidence).
    None when neither exists.
    """
    chain_cards = [
        c
        for c in cards
        if c.category in (EvidenceCategory.SUPPLY_BOTTLENECK, EvidenceCategory.DEMAND_SIGNAL)
    ]
    base = _mean([c.confidence for c in chain_cards])
    if argument is not None:
        agent_score = argument.confidence * (
            1.0 if argument.direction != Direction.NEGATIVE else 0.5
        )
        return agent_score if base is None else 0.5 * base + 0.5 * agent_score
    return base


def market_confirmation(sector_features: pd.DataFrame) -> float | None:
    """Mean cross-sectional 63d-return rank of the sector's securities."""
    if sector_features.empty or "rank_ret_63d" not in sector_features:
        return None
    mean = sector_features["rank_ret_63d"].mean()
    return float(mean) if pd.notna(mean) else None


def liquidity(sector_features: pd.DataFrame) -> float | None:
    """Mean cross-sectional dollar-volume rank of the sector's securities."""
    if sector_features.empty or "rank_dollar_volume" not in sector_features:
        return None
    mean = sector_features["rank_dollar_volume"].mean()
    return float(mean) if pd.notna(mean) else None


def crowding_risk(sector_features: pd.DataFrame) -> float | None:
    """Momentum-extreme proxy for crowding: how stretched the sector's 63d
    ranks already are. (No PIT short-interest feed is configured; when one
    exists it should replace this proxy.)"""
    if sector_features.empty or "rank_ret_63d" not in sector_features:
        return None
    mean = sector_features["rank_ret_63d"].mean()
    if pd.isna(mean):
        return None
    return float(max(0.0, min(1.0, (float(mean) - 0.5) * 2.0)))  # only the top half is "crowded"


def fundamental_confirmation(
    pit_fundamentals: list | None, argument: AgentArgument | None
) -> float | None:
    """PIT-safe fundamental trend confirmation.

    Strict mode: current-snapshot reference data is NOT admissible for
    historical as-of dates (the gatekeeper rejects it), so this component is
    honestly None for historical runs unless vintage-safe records exist.
    """
    if not pit_fundamentals:
        return None
    # With real PIT records: fraction of tracked metrics whose latest value
    # improved vs the prior observation, weighted by the specialist's view.
    by_metric: dict[str, list] = {}
    for rec in pit_fundamentals:
        by_metric.setdefault(rec.metric, []).append(rec)
    improved = total = 0
    for records in by_metric.values():
        records = sorted(records, key=lambda r: r.usable_from)
        if len(records) < 2:
            continue
        total += 1
        if records[-1].value > records[-2].value:
            improved += 1
    if total == 0:
        return None
    measured = improved / total
    if argument is not None:
        return 0.5 * measured + 0.5 * argument.confidence
    return measured


def valuation_risk(
    valuation_features: dict[str, float] | None, argument: AgentArgument | None
) -> float | None:
    """Valuation risk from PIT-safe valuation metrics (e.g. PE percentile),
    else the valuation specialist's signed conviction. None when neither."""
    if valuation_features:
        pe_pct = valuation_features.get("pe_percentile")
        if pe_pct is not None:
            return float(max(0.0, min(1.0, pe_pct)))
    if argument is not None:
        # valuation specialist: high confidence + negative direction = high risk
        if argument.direction == Direction.NEGATIVE:
            return argument.confidence
        if argument.direction == Direction.POSITIVE:
            return 1.0 - argument.confidence
        return 0.5 * (1.0 - argument.confidence) + 0.25  # neutral/mixed: mild risk
    return None


def macro_alignment(argument: AgentArgument | None, cards: list[EvidenceCard]) -> float | None:
    """Macro backdrop alignment: the macro specialist's signed conviction,
    falling back to the signed balance of macro-signal evidence cards."""
    if argument is not None:
        if argument.direction == Direction.POSITIVE:
            return argument.confidence
        if argument.direction == Direction.NEGATIVE:
            return 1.0 - argument.confidence
        return 0.5
    macro_cards = [c for c in cards if c.category == EvidenceCategory.MACRO_SIGNAL]
    if not macro_cards:
        return None
    signed = sum(
        c.confidence * (1.0 if c.direction == Direction.POSITIVE else -1.0 if c.direction == Direction.NEGATIVE else 0.0)
        for c in macro_cards
    ) / len(macro_cards)
    return max(0.0, min(1.0, 0.5 + 0.5 * signed))


def validation_strength(score: float) -> float:
    """Deterministic transform of the judge/debate verdict score."""
    return score


def company_factors(
    tickers: list[str],
    sector_cards: list[EvidenceCard],
    features: pd.DataFrame,
) -> dict[str, float]:
    """Per-company differentiation inside a selected sector.

    factor = mean of the available per-company measurements:
      evidence exposure (mean confidence*relevance of cards naming the
      company), momentum rank (rank_ret_63d), liquidity rank
      (rank_dollar_volume). A company with no measurements is omitted (the
      signal engine treats omission as neutral 0.5).
    """
    out: dict[str, float] = {}
    feat = features.set_index("ticker") if not features.empty else features
    for ticker in tickers:
        parts: list[float] = []
        own = [c for c in sector_cards if ticker in c.securities]
        if own:
            parts.append(min(1.0, _mean([c.confidence * c.relevance for c in own]) or 0.0))
        if not feat.empty and ticker in feat.index:
            row = feat.loc[ticker]
            for col in ("rank_ret_63d", "rank_dollar_volume"):
                if col in feat.columns and pd.notna(row[col]):
                    parts.append(float(row[col]))
        if parts:
            out[ticker] = sum(parts) / len(parts)
    return out


_PACKAGE_FEATURE_COLS = (
    "ret_21d",
    "ret_63d",
    "rank_ret_63d",
    "benchmark_rel_ret_21d",
    "realized_vol_21d",
    "drawdown",
    "rank_dollar_volume",
)


def package_features(features: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Bounded per-ticker feature VALUES for agent prompts (never a bare path)."""
    out: dict[str, dict[str, float]] = {}
    if features.empty:
        return out
    for row in features.itertuples():
        values: dict[str, float] = {}
        for col in _PACKAGE_FEATURE_COLS:
            value = getattr(row, col, None)
            if value is not None and pd.notna(value):
                values[col] = float(value)
        if values:
            out[str(row.ticker)] = values
    return out
