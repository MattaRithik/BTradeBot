"""Stage E: evidence engine, thesis builder, mapping, scoring, validation, ranking."""

from __future__ import annotations

import pytest
from tests.conftest import AS_OF, dt, make_bar, make_evidence, make_news

from quant_platform.core.enums import Direction, ValidationStatus
from quant_platform.core.schemas import (
    AgentArgument,
    EvidencePackage,
    ScoreBreakdown,
    SectorSubmission,
    ValidationResult,
)
from quant_platform.models import MockModelProvider
from quant_platform.research import (
    EvidenceEngine,
    ScoringConfig,
    TradabilityFilters,
    build_thesis,
    check_tradability,
    compute_score,
    evidence_stats,
    group_evidence_by_sector,
    load_scoring_config,
    map_sector_etfs,
    map_sector_securities,
    rank_sectors,
    validate_thesis,
)


def _argument(name: str, **overrides) -> dict:
    payload = {
        "agent_name": name,
        "conclusion": f"{name} view",
        "confidence": 0.5,
        "direction": "neutral",
        "as_of_date": "2024-12-31",
    }
    payload.update(overrides)
    return payload


def _package() -> EvidencePackage:
    return EvidencePackage(
        run_id="run1",
        as_of_date=AS_OF,
        evidence=[make_evidence()],
        news=[make_news()],
    )


def _submission(
    sector: str,
    composite: float,
    status: ValidationStatus = ValidationStatus.APPROVED,
    leakage: bool = False,
) -> SectorSubmission:
    thesis = build_thesis(sector, [make_evidence()], None, AS_OF)
    validation = ValidationResult(
        thesis_id=thesis.thesis_id,
        status=status,
        leakage_detected=leakage,
        score=composite,
        as_of_date=AS_OF,
    )
    scores = ScoreBreakdown().model_copy(update={"composite": composite})
    return SectorSubmission(
        thesis=thesis, validation=validation, scores=scores, composite_score=composite
    )


class TestEvidenceEngine:
    async def test_extracts_cards_with_provenance(self):
        news = make_news("n1", usable=dt(2024, 6, 3))
        scripted = {
            "evidence_extraction": {
                "cards": [
                    {
                        "news_id": "n1",
                        "claim": "HBM supply sold out through 2025",
                        "category": "supply_bottleneck",
                        "direction": "positive",
                        "confidence": 0.8,
                        "relevance": 0.9,
                        "securities": ["MU"],
                        "sectors": ["Memory & Storage"],
                    }
                ]
            }
        }
        engine = EvidenceEngine(MockModelProvider(scripted=scripted))
        cards = await engine.extract([news], AS_OF)
        assert len(cards) == 1
        card = cards[0]
        assert card.source_ref == "n1"  # cites the source item
        assert card.usable_from == news.usable_from  # PIT copied from the news
        assert card.published_at == news.published_at
        assert card.claim == "HBM supply sold out through 2025"

    async def test_hallucinated_news_id_dropped(self):
        scripted = {
            "evidence_extraction": {
                "cards": [
                    {
                        "news_id": "ghost",
                        "claim": "invented citation",
                        "category": "demand_signal",
                        "direction": "positive",
                        "confidence": 0.9,
                        "relevance": 0.9,
                    }
                ]
            }
        }
        engine = EvidenceEngine(MockModelProvider(scripted=scripted))
        assert await engine.extract([make_news("n1")], AS_OF) == []

    async def test_empty_news_returns_empty(self):
        engine = EvidenceEngine(MockModelProvider())
        assert await engine.extract([], AS_OF) == []

    def test_group_and_stats(self):
        c1 = make_evidence("e1")
        grouped = group_evidence_by_sector([c1])
        assert list(grouped) == ["AI Infrastructure"]
        stats = evidence_stats([c1])
        assert stats["count"] == 1
        assert stats["mean_confidence"] == pytest.approx(0.8)
        assert evidence_stats([])["count"] == 0


class TestThesisBuilder:
    def test_assembles_from_argument_and_evidence(self):
        evidence = [make_evidence("e1")]
        argument = AgentArgument(
            agent_name="sector",
            conclusion="AI infra capex supercycle intact",
            confidence=0.75,
            direction=Direction.POSITIVE,
            evidence_ids=["e1"],
            risks=["capex digestion"],
            as_of_date=AS_OF,
        )
        thesis = build_thesis("AI Infrastructure", evidence, argument, AS_OF)
        assert thesis.thesis_summary == "AI infra capex supercycle intact"
        assert thesis.confidence == pytest.approx(0.75)
        assert len(thesis.causal_chain) == 1
        assert thesis.causal_chain[0].relation == "drives"  # DEMAND_SIGNAL drives theme
        assert len(thesis.causal_nodes) == 2  # theme + evidence node
        assert "capex digestion" in thesis.risks
        assert thesis.candidate_securities == ["NVDA"]
        assert thesis.evidence_ids == ["e1"]

    def test_empty_evidence_still_valid(self):
        thesis = build_thesis("Robotics / Physical AI", [], None, AS_OF)
        assert thesis.confidence == 0.0
        assert thesis.causal_chain == []
        assert thesis.candidate_securities == []

    def test_invalidation_conditions_from_negative_risk_cards(self):
        from quant_platform.core.enums import EvidenceCategory

        risk_card = make_evidence("r1").model_copy(
            update={"category": EvidenceCategory.RISK_SIGNAL, "direction": Direction.NEGATIVE}
        )
        thesis = build_thesis("AI Infrastructure", [risk_card], None, AS_OF)
        assert thesis.invalidation_conditions == [risk_card.claim]


class TestTradability:
    def _bars(self, n: int, close: float = 100.0, volume: int = 1_000_000):
        # 2024-01-02 + n calendar days stays inside 2024 for n <= 364
        from datetime import timedelta

        base = dt(2024, 1, 2)
        return [
            make_bar(ts=base + timedelta(days=i), close=close, ).model_copy(
                update={"volume": volume}
            )
            for i in range(n)
        ]

    def test_tradable(self):
        result = check_tradability("NVDA", self._bars(130), AS_OF)
        assert result.tradable
        assert result.reasons == []
        assert result.history_days == 130
        assert result.last_price == pytest.approx(100.0)

    def test_insufficient_history(self):
        result = check_tradability("NEW", self._bars(50), AS_OF)
        assert not result.tradable
        assert any("insufficient history" in r for r in result.reasons)

    def test_price_floor(self):
        result = check_tradability("PENNY", self._bars(130, close=1.0), AS_OF)
        assert not result.tradable
        assert any("below floor" in r for r in result.reasons)

    def test_illiquid(self):
        result = check_tradability("THIN", self._bars(130, volume=10), AS_OF)
        assert not result.tradable
        assert any("dollar volume" in r for r in result.reasons)

    def test_no_bars(self):
        result = check_tradability("NONE", [], AS_OF)
        assert not result.tradable
        assert "no bars at all" in result.reasons

    def test_filters_override(self):
        relaxed = TradabilityFilters(min_history_days=10)
        assert check_tradability("X", self._bars(20), AS_OF, filters=relaxed).tradable


class TestMapping:
    def test_securities_direct_vs_watchlist(self):
        universe = {"ai_infrastructure": {"securities": ["NVDA", "AVGO"], "etfs": ["SMH"]}}
        mappings = map_sector_securities(
            "ai_infrastructure", "AI Infrastructure", AS_OF,
            universe=universe, evidence_tickers={"NVDA"},
        )
        by_ticker = {m.ticker: m for m in mappings}
        assert by_ticker["NVDA"].exposure.value == "direct"
        assert by_ticker["AVGO"].exposure.value == "watchlist"

    def test_etfs_indirect(self):
        universe = {"ai_infrastructure": {"securities": [], "etfs": ["SMH", "SOXX"]}}
        etfs = map_sector_etfs("ai_infrastructure", "AI Infrastructure", AS_OF, universe=universe)
        assert [e.etf_ticker for e in etfs] == ["SMH", "SOXX"]
        assert all(e.exposure.value == "indirect" for e in etfs)


class TestScoring:
    def test_real_config_loads_and_weights_sum_to_one(self):
        cfg = load_scoring_config()
        assert sum(cfg.weights.values()) == pytest.approx(1.0)
        assert cfg.selection_threshold == pytest.approx(0.55)

    def test_composite_math_with_risk_subtraction(self):
        cfg = load_scoring_config()
        all_ones = dict.fromkeys(cfg.weights, 1.0)
        breakdown = compute_score(all_ones, cfg)
        # positive weights sum to 0.85; risk weights (0.15) subtracted
        assert breakdown.composite == pytest.approx(0.70)

    def test_all_zeros_is_zero(self):
        cfg = load_scoring_config()
        assert compute_score({}, cfg).composite == 0.0

    def test_risk_alone_clamps_to_zero(self):
        cfg = load_scoring_config()
        assert compute_score({"valuation_risk": 1.0}, cfg).composite == 0.0

    def test_out_of_range_component_raises(self):
        with pytest.raises(ValueError, match="out of"):
            compute_score({"trend_strength": 1.5})

    def test_bad_weight_sum_rejected(self):
        with pytest.raises(ValueError, match=r"sum to 1\.0"):
            ScoringConfig(weights={"trend_strength": 0.5})

    def test_unknown_component_rejected(self):
        with pytest.raises(ValueError, match="unknown scoring component"):
            ScoringConfig(weights={"trend_strength": 0.5, "bogus": 0.5})


class TestValidation:
    async def test_approved_path_and_audit(self, audit):
        scripted = {
            "bull": _argument("bull", direction="positive", confidence=0.8),
            "judge": _argument("judge", direction="positive", confidence=0.8,
                               conclusion="bull case is stronger"),
        }
        thesis = build_thesis("AI Infrastructure", [make_evidence()], None, AS_OF)
        result = await validate_thesis(thesis, _package(), MockModelProvider(scripted=scripted),
                                       audit=audit)
        assert result.status == ValidationStatus.APPROVED
        assert not result.leakage_detected
        assert result.judge_rationale == "bull case is stronger"
        from quant_platform.core.enums import AuditEventType

        assert audit.count_by_type(AuditEventType.VALIDATION_DECISION) == 1

    async def test_leakage_forces_rejected(self):
        scripted = {
            "judge": _argument("judge", direction="positive", confidence=0.9),
            "leakage": _argument("leakage", direction="negative", confidence=0.9,
                                 conclusion="used post-cutoff data"),
        }
        thesis = build_thesis("AI Infrastructure", [make_evidence()], None, AS_OF)
        result = await validate_thesis(thesis, _package(), MockModelProvider(scripted=scripted))
        assert result.status == ValidationStatus.REJECTED
        assert result.leakage_detected

    async def test_negative_judge_rejects(self):
        scripted = {"judge": _argument("judge", direction="negative", confidence=0.7)}
        thesis = build_thesis("AI Infrastructure", [make_evidence()], None, AS_OF)
        result = await validate_thesis(thesis, _package(), MockModelProvider(scripted=scripted))
        assert result.status == ValidationStatus.REJECTED

    async def test_missing_evidence_needs_more(self):
        scripted = {
            "judge": _argument("judge", direction="positive", confidence=0.8,
                               missing_evidence=["revenue confirmation"]),
        }
        thesis = build_thesis("AI Infrastructure", [make_evidence()], None, AS_OF)
        result = await validate_thesis(thesis, _package(), MockModelProvider(scripted=scripted))
        assert result.status == ValidationStatus.NEEDS_MORE_EVIDENCE

    async def test_neutral_judge_is_watchlist(self):
        thesis = build_thesis("AI Infrastructure", [make_evidence()], None, AS_OF)
        result = await validate_thesis(thesis, _package(), MockModelProvider())
        assert result.status == ValidationStatus.WATCHLIST  # canned judge: neutral, 0.5

    async def test_judge_sees_debate_verbatim(self):
        provider = MockModelProvider(scripted={
            "bull": _argument("bull", conclusion="demand is real"),
        })
        thesis = build_thesis("AI Infrastructure", [make_evidence()], None, AS_OF)
        await validate_thesis(thesis, _package(), provider)
        judge_calls = [c for c in provider.calls if c.task == "judge"]
        assert len(judge_calls) == 1
        assert "BULL" in judge_calls[0].user_prompt
        assert "demand is real" in judge_calls[0].user_prompt


class TestRanking:
    def test_orders_by_composite_desc(self):
        result = rank_sectors(
            [_submission("B", 0.7), _submission("A", 0.9), _submission("C", 0.6)],
            "run1", AS_OF,
        )
        assert [r.sector for r in result.leaderboard] == ["A", "B", "C"]
        assert [r.rank for r in result.leaderboard] == [1, 2, 3]

    def test_rejected_never_selected(self):
        result = rank_sectors(
            [_submission("A", 0.95, status=ValidationStatus.REJECTED)], "run1", AS_OF
        )
        assert not result.leaderboard[0].selected
        assert "rejected" in result.leaderboard[0].rationale

    def test_below_threshold_not_selected(self):
        result = rank_sectors([_submission("A", 0.40)], "run1", AS_OF)
        assert not result.leaderboard[0].selected

    def test_choose_nothing_is_explicit(self):
        result = rank_sectors(
            [_submission("A", 0.40), _submission("B", 0.30, status=ValidationStatus.WATCHLIST)],
            "run1", AS_OF,
        )
        assert not any(r.selected for r in result.leaderboard)
        assert "NOTHING" in result.selection_rationale

    def test_leakage_excluded_even_if_approved(self):
        sub = _submission("A", 0.9)
        sub = sub.model_copy(
            update={"validation": sub.validation.model_copy(update={"leakage_detected": True})}
        )
        result = rank_sectors([sub], "run1", AS_OF)
        assert not result.leaderboard[0].selected
