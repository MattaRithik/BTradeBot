"""Measured score components + missing-data policy + company differentiation."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from conftest import make_evidence
from quant_platform.core.enums import Direction
from quant_platform.core.schemas import AgentArgument
from quant_platform.research.components import (
    company_factors,
    crowding_risk,
    evidence_quality,
    macro_alignment,
    supply_chain_confidence,
)
from quant_platform.research.scoring import ScoringConfig, compute_score

_CFG = ScoringConfig(
    weights={"trend_strength": 0.5, "evidence_quality": 0.3, "valuation_risk": 0.2},
    risk_subtracted=["valuation_risk"],
    selection_threshold=0.5,
    completeness_penalty=0.5,
)


class TestMissingComponentPolicy:
    def test_missing_components_renormalize_and_penalize(self):
        # evidence_quality missing (0.3 mass): renormalized over the rest
        # then penalized by (1 - 0.5*0.3) = 0.85
        scores = compute_score({"trend_strength": 0.8, "evidence_quality": None, "valuation_risk": 0.2}, _CFG)
        assert scores.missing_components == ["evidence_quality"]
        assert scores.data_completeness == pytest.approx(0.7)
        expected = ((0.5 / 0.7) * 0.8 - (0.2 / 0.7) * 0.2) * 0.85
        assert scores.composite == pytest.approx(expected, abs=1e-9)

    def test_all_measured_no_penalty(self):
        scores = compute_score({"trend_strength": 0.8, "evidence_quality": 0.6, "valuation_risk": 0.2}, _CFG)
        assert scores.data_completeness == 1.0
        assert scores.composite == pytest.approx(0.5 * 0.8 + 0.3 * 0.6 - 0.2 * 0.2)

    def test_missing_is_not_silently_zero(self):
        # a measured 0.5 vs a missing component must differ
        measured = compute_score({"trend_strength": 0.5, "evidence_quality": 0.5, "valuation_risk": 0.5}, _CFG)
        missing = compute_score({"trend_strength": 0.5, "evidence_quality": None, "valuation_risk": 0.5}, _CFG)
        assert measured.composite != missing.composite
        assert "evidence_quality" in missing.missing_components

    def test_out_of_range_still_rejected(self):
        with pytest.raises(ValueError, match="out of"):
            compute_score({"trend_strength": 1.5, "evidence_quality": 0.5, "valuation_risk": 0.5}, _CFG)


def _arg(confidence: float, direction: Direction) -> AgentArgument:
    return AgentArgument(
        agent_name="t", conclusion="c", confidence=confidence, direction=direction,
        as_of_date=date(2024, 12, 31),
    )


class TestComponentCalculators:
    def test_evidence_quality_none_without_cards(self):
        assert evidence_quality([]) is None

    def test_supply_chain_from_cards_and_agent(self):
        from quant_platform.core.enums import EvidenceCategory

        card = make_evidence().model_copy(update={"category": EvidenceCategory.SUPPLY_BOTTLENECK})
        base = supply_chain_confidence([card], None)
        assert base == pytest.approx(card.confidence)
        mixed = supply_chain_confidence([card], _arg(0.6, Direction.POSITIVE))
        assert mixed == pytest.approx(0.5 * card.confidence + 0.5 * 0.6)

    def test_crowding_risk_from_momentum_extreme(self):
        features = pd.DataFrame({"ticker": ["A"], "rank_ret_63d": [0.95]})
        assert crowding_risk(features) == pytest.approx(0.9)
        low = pd.DataFrame({"ticker": ["A"], "rank_ret_63d": [0.4]})
        assert crowding_risk(low) == 0.0

    def test_macro_alignment_signed(self):
        assert macro_alignment(_arg(0.8, Direction.POSITIVE), []) == pytest.approx(0.8)
        assert macro_alignment(_arg(0.8, Direction.NEGATIVE), []) == pytest.approx(0.2)
        assert macro_alignment(None, []) is None

    def test_company_factors_differentiate(self):
        cards = [
            make_evidence("e1").model_copy(update={"securities": ["NVDA"], "confidence": 0.9, "relevance": 0.9}),
        ]
        features = pd.DataFrame(
            {
                "ticker": ["NVDA", "AMD"],
                "rank_ret_63d": [0.9, 0.2],
                "rank_dollar_volume": [0.8, 0.3],
            }
        )
        factors = company_factors(["NVDA", "AMD"], cards, features)
        assert factors["NVDA"] > factors["AMD"]  # differentiated, never identical
        assert 0.0 <= factors["AMD"] <= 1.0
