"""Stage F portfolio: strategy builders + risk constraints."""

from __future__ import annotations

import pandas as pd
import pytest
from tests.conftest import AS_OF

from quant_platform.core.enums import SignalClass, TargetType
from quant_platform.core.schemas import Signal
from quant_platform.portfolio import (
    BUILDERS,
    RiskConfig,
    apply_risk_constraints,
    build_strategy,
)
from quant_platform.portfolio.builders import (
    build_cash,
    build_ensemble,
    build_etf_rotation,
    build_long_basket,
    build_long_short,
    build_momentum,
    build_risk_parity,
    build_score_weighted,
)


def _signal(target: str, score: float = 0.8, sector: str = "AI Infrastructure",
            cls: SignalClass = SignalClass.STRONG_LONG, actionable: bool = True,
            target_type: TargetType = TargetType.SECURITY,
            ticker: str | None = None) -> Signal:
    if ticker is None and target_type in (TargetType.SECURITY, TargetType.ETF):
        ticker = target
    return Signal(
        signal_id=f"sig_{target}_{cls.value}",
        target=target,
        target_type=target_type,
        sector=sector,
        ticker=ticker,
        raw_score=score,
        confidence=0.8,
        signal_class=cls,
        action_allowed=actionable,
        as_of_date=AS_OF,
    )


def _features(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


LONGS = [_signal("NVDA", 0.9), _signal("AVGO", 0.6), _signal("ANET", 0.3)]


class TestBuilders:
    def test_long_basket_equal_weight(self):
        target = build_long_basket(LONGS, None, "run1", AS_OF)
        assert len(target.positions) == 3
        assert all(p.weight == pytest.approx(1 / 3) for p in target.positions)
        assert target.gross_exposure == pytest.approx(1.0)
        assert target.cash_weight == pytest.approx(0.0)

    def test_score_weighted_proportional(self):
        target = build_score_weighted(LONGS, None, "run1", AS_OF)
        weights = {p.ticker: p.weight for p in target.positions}
        assert weights["NVDA"] == pytest.approx(0.9 / 1.8)
        assert weights["ANET"] == pytest.approx(0.3 / 1.8)

    def test_etf_rotation_concentrates(self):
        signals = [
            _signal("SMH", 0.8, target_type=TargetType.ETF),
            _signal("XLU", 0.6, sector="Data Center Power", target_type=TargetType.ETF),
        ]
        target = build_etf_rotation(signals, None, "run1", AS_OF)
        assert len(target.positions) == 1
        assert target.positions[0].ticker == "SMH"
        assert target.positions[0].weight == pytest.approx(1.0)

    def test_long_short_has_negative_leg_and_warning(self):
        signals = [*LONGS, _signal("TER", 0.2, sector="Robotics / Physical AI",
                                   cls=SignalClass.AVOID)]
        target = build_long_short(signals, None, "run1", AS_OF)
        assert any(p.weight < 0 for p in target.positions)
        assert "short" in target.warnings  # schema requires the explicit flag
        assert target.gross_exposure == pytest.approx(1.0)

    def test_momentum_uses_ranks(self):
        features = _features([
            {"ticker": "NVDA", "rank_ret_63d": 0.9},
            {"ticker": "AVGO", "rank_ret_63d": 0.5},
            {"ticker": "ANET", "rank_ret_63d": 0.1},
        ])
        target = build_momentum(LONGS, features, "run1", AS_OF)
        weights = {p.ticker: p.weight for p in target.positions}
        assert weights["NVDA"] == pytest.approx(0.9 / 1.5)

    def test_risk_parity_inverse_vol(self):
        features = _features([
            {"ticker": "NVDA", "realized_vol_21d": 0.6},
            {"ticker": "AVGO", "realized_vol_21d": 0.3},
            {"ticker": "ANET", "realized_vol_21d": 0.3},
        ])
        target = build_risk_parity(LONGS, features, "run1", AS_OF)
        weights = {p.ticker: p.weight for p in target.positions}
        # inv vols: 1/0.6, 1/0.3, 1/0.3 -> NVDA gets 1/5
        assert weights["NVDA"] == pytest.approx((1 / 0.6) / (1 / 0.6 + 2 / 0.3))

    def test_ensemble_averages(self):
        features = _features([
            {"ticker": t, "rank_ret_63d": r, "realized_vol_21d": v}
            for t, r, v in [("NVDA", 0.9, 0.6), ("AVGO", 0.5, 0.3), ("ANET", 0.1, 0.3)]
        ])
        target = build_ensemble(LONGS, features, "run1", AS_OF)
        assert set(p.ticker for p in target.positions) == {"NVDA", "AVGO", "ANET"}
        assert target.gross_exposure == pytest.approx(1.0)

    def test_no_longs_means_cash(self):
        target = build_long_basket([], None, "run1", AS_OF)
        assert target.strategy == "cash"
        assert target.positions == []
        assert target.cash_weight == pytest.approx(1.0)

    def test_cash_builder_explicit(self):
        target = build_cash(LONGS, None, "run1", AS_OF)
        assert target.gross_exposure == 0.0
        assert target.cash_weight == 1.0

    def test_non_actionable_signals_ignored(self):
        labels = [_signal("AI Infrastructure", actionable=False,
                          target_type=TargetType.SECTOR)]
        target = build_long_basket(labels, None, "run1", AS_OF)
        assert target.strategy == "cash"

    def test_registry_and_unknown(self):
        assert set(BUILDERS) == {"long_basket", "score_weighted", "etf_rotation",
                                 "long_short", "momentum", "risk_parity",
                                 "ensemble", "cash"}
        target = build_strategy("ensemble", LONGS, None, "run1", AS_OF)
        assert target.strategy == "ensemble"
        with pytest.raises(KeyError, match="unknown strategy"):
            build_strategy("yolo", LONGS, None, "run1", AS_OF)


def _raw_target(weights: dict[str, tuple[float, str]]) -> object:
    from quant_platform.portfolio.builders import _target

    # raw (pre-risk) targets may exceed 100% gross; the schema requires an
    # explicit leverage/short flag for that — risk constraints then clamp it
    return _target("test", "run1", AS_OF,
                   {t: (w, s, "test") for t, (w, s) in weights.items()},
                   warnings=["leverage"])


class TestRiskConstraints:
    def test_ticker_cap(self):
        target = _raw_target({"NVDA": (0.5, "A"), "AVGO": (0.5, "A")})
        out = apply_risk_constraints(target, RiskConfig(max_ticker_weight=0.15))
        assert all(abs(p.weight) <= 0.15 + 1e-12 for p in out.positions)
        assert any("capped" in w for w in out.warnings)

    def test_sector_cap_scales_proportionally(self):
        target = _raw_target({"NVDA": (0.2, "A"), "AVGO": (0.2, "A"), "TER": (0.2, "B")})
        out = apply_risk_constraints(target, RiskConfig(max_ticker_weight=0.5,
                                                        max_sector_weight=0.3))
        sector_a = sum(p.weight for p in out.positions if p.sector == "A")
        assert sector_a == pytest.approx(0.3)
        ter = next(p for p in out.positions if p.ticker == "TER")
        assert ter.weight == pytest.approx(0.2)  # other sector untouched

    def test_gross_cap(self):
        target = _raw_target({"NVDA": (0.4, "A"), "AVGO": (0.4, "B"), "ANET": (0.4, "C")})
        out = apply_risk_constraints(target, RiskConfig(max_ticker_weight=0.5,
                                                        max_gross_exposure=0.6))
        assert out.gross_exposure == pytest.approx(0.6)
        assert out.cash_weight == pytest.approx(0.4)

    def test_shorts_dropped_when_disabled(self):
        target = _raw_target({"NVDA": (0.5, "A"), "TER": (-0.5, "B")})
        target = target.model_copy(update={"warnings": ["short"]})
        out = apply_risk_constraints(target, RiskConfig(allow_shorting=False))
        assert all(p.weight >= 0 for p in out.positions)
        assert any("shorting disabled" in w for w in out.warnings)

    def test_max_positions_keeps_largest(self):
        target = _raw_target({f"T{i}": (0.01 * (i + 1), "A") for i in range(5)})
        out = apply_risk_constraints(target, RiskConfig(max_positions=2,
                                                        max_sector_weight=1.0))
        assert len(out.positions) == 2
        assert {p.ticker for p in out.positions} == {"T3", "T4"}

    def test_liquidity_floor(self):
        features = _features([
            {"ticker": "NVDA", "avg_dollar_volume_21d": 1e8},
            {"ticker": "THIN", "avg_dollar_volume_21d": 1e3},
        ])
        target = _raw_target({"NVDA": (0.1, "A"), "THIN": (0.1, "A")})
        out = apply_risk_constraints(target, RiskConfig(max_sector_weight=1.0), features)
        assert {p.ticker for p in out.positions} == {"NVDA"}

    def test_volatility_target_scales_down(self):
        features = _features([
            {"ticker": "NVDA", "realized_vol_21d": 0.8},
            {"ticker": "AVGO", "realized_vol_21d": 0.8},
        ])
        target = _raw_target({"NVDA": (0.4, "A"), "AVGO": (0.4, "B")})
        out = apply_risk_constraints(
            target, RiskConfig(max_ticker_weight=0.5, volatility_target_annual=0.4), features
        )
        # port vol = 0.8 * 0.8 = 0.64 > 0.4 -> scale 0.625
        assert out.gross_exposure == pytest.approx(0.8 * 0.4 / 0.64)

    def test_full_cash_survives_untouched(self):
        target = _raw_target({})
        out = apply_risk_constraints(target, RiskConfig())
        assert out.cash_weight == 1.0
        assert out.positions == []
