"""Stage G backtest: splits, engine realism, metrics."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from quant_platform.backtest import (
    BacktestConfig,
    WalkForwardConfig,
    compute_metrics,
    make_walkforward_splits,
    run_backtest,
)
from quant_platform.core.enums import AuditEventType
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget, PredictionSnapshot
from quant_platform.snapshots import freeze_snapshot


class TestSplits:
    def test_rolling_windows(self):
        cfg = WalkForwardConfig(lookback_months=24, test_months=2, step_months=2)
        splits = make_walkforward_splits(date(2023, 12, 31), date(2024, 6, 30), cfg)
        assert len(splits) == 4  # Dec, Feb, Apr, Jun
        first = splits[0]
        assert first.lookback_start == date(2021, 12, 31)
        assert first.as_of_date == date(2023, 12, 31)
        assert first.test_start == date(2024, 1, 1)
        assert first.test_end == date(2024, 2, 29)
        assert splits[1].as_of_date == date(2024, 2, 29)

    def test_unique_ids(self):
        splits = make_walkforward_splits(date(2024, 1, 31), date(2024, 5, 31))
        assert len({s.split_id for s in splits}) == len(splits)


def _prices() -> pd.DataFrame:
    """Synthetic uptrend: A +1%/day, B flat, SPY +0.2%/day over 2024-2025."""
    days = pd.bdate_range("2024-06-01", "2025-03-15", tz="UTC")
    rows = []
    for i, ts in enumerate(days):
        rows.append({"ticker": "A", "timestamp": ts, "close": 100.0 * 1.01**i})
        rows.append({"ticker": "B", "timestamp": ts, "close": 50.0})
        rows.append({"ticker": "SPY", "timestamp": ts, "close": 400.0 * 1.002**i})
    return pd.DataFrame(rows)


def _snapshot(weights: dict[str, float] | None) -> PredictionSnapshot:
    ctx = ResearchContext(
        run_id="run1",
        as_of_date=date(2024, 12, 31),
        visible_start=date(2024, 6, 1),
        visible_end=date(2024, 12, 31),
        test_start=date(2025, 1, 1),
        test_end=date(2025, 2, 28),
    )
    portfolio = None
    if weights is not None:
        gross = sum(abs(w) for w in weights.values())
        portfolio = PortfolioTarget(
            target_id="tgt_test",
            run_id="run1",
            strategy="test",
            as_of_date=date(2024, 12, 31),
            positions=[PortfolioPosition(ticker=t, weight=w) for t, w in weights.items()],
            cash_weight=1.0 - gross,
            gross_exposure=gross,
            net_exposure=gross,
        )
    return freeze_snapshot(ctx, portfolio=portfolio)


def _split() -> object:
    from quant_platform.core.schemas import WalkForwardSplit

    return WalkForwardSplit(
        split_id="split_test",
        lookback_start=date(2024, 6, 1),
        as_of_date=date(2024, 12, 31),
        test_start=date(2025, 1, 1),
        test_end=date(2025, 2, 28),
    )


class TestEngine:
    def test_uptrend_book_gains_and_beats_flat_benchmark(self):
        result = run_backtest(_snapshot({"A": 1.0}), _split(), _prices(),
                              BacktestConfig(benchmarks=["SPY"]))
        assert result.metrics.cumulative_return > 0.3  # ~1%/day for ~2 months, minus costs
        assert result.metrics.transaction_costs > 0
        assert result.metrics.turnover == pytest.approx(1.0)
        assert result.benchmarks["SPY"] > 0
        assert result.metrics.benchmark_excess_return["SPY"] > 0
        assert "equal_weight_universe" in result.benchmarks
        assert "simple_momentum" in result.benchmarks

    def test_execution_delay_respected(self):
        prices = _prices()
        result = run_backtest(_snapshot({"A": 1.0}), _split(), prices,
                              BacktestConfig(benchmarks=[], execution_delay_days=7))
        # entry = first bar >= 2025-01-08; contribution holding window shrinks
        a_contrib = next(c for c in result.contributions if c.ticker == "A")
        full_days = len(pd.bdate_range("2025-01-01", "2025-02-28"))
        assert a_contrib.holding_days < full_days

    def test_flat_position_is_pure_cash(self):
        result = run_backtest(_snapshot({"B": 1.0}), _split(), _prices(),
                              BacktestConfig(benchmarks=[]))
        # B never moves: return is just costs (negative) on ~zero volatility
        assert result.metrics.cumulative_return < 0
        assert abs(result.metrics.cumulative_return) < 0.01

    def test_empty_portfolio_earns_cash_rate(self):
        result = run_backtest(_snapshot(None), _split(), _prices(),
                              BacktestConfig(benchmarks=[], cash_return_annual=0.04))
        assert result.metrics.cumulative_return > 0
        assert result.contributions == []

    def test_no_data_in_window_raises(self):
        prices = _prices()
        prices = prices[prices["timestamp"] < pd.Timestamp("2024-12-01", tz="UTC")]
        with pytest.raises(ValueError, match="no price data"):
            run_backtest(_snapshot({"A": 1.0}), _split(), prices, BacktestConfig())

    def test_contributions_sum_to_portfolio_return(self):
        result = run_backtest(_snapshot({"A": 0.6, "B": 0.4}), _split(), _prices(),
                              BacktestConfig(benchmarks=[], commission_per_share=0,
                                             min_commission_per_order=0, slippage_bps=0))
        assert sum(c.return_contribution for c in result.contributions) == pytest.approx(
            result.metrics.cumulative_return, abs=0.01
        )

    def test_persisted_and_audited(self, store, audit):
        result = run_backtest(_snapshot({"A": 1.0}), _split(), _prices(),
                              BacktestConfig(benchmarks=[]), store=store, audit=audit)
        assert result.daily_returns_path
        assert audit.count_by_type(AuditEventType.BACKTEST_STARTED) == 1
        assert audit.count_by_type(AuditEventType.BACKTEST_COMPLETED) == 1


class TestMetrics:
    def test_constant_positive_returns(self):
        rets = pd.Series([0.001] * 100)
        m = compute_metrics(rets, risk_free_annual=0.0)
        assert m.hit_rate == 1.0
        assert m.max_drawdown == 0.0
        assert m.sharpe == 0.0  # zero volatility -> defined as 0
        assert m.cumulative_return == pytest.approx(1.001**100 - 1)

    def test_drawdown_and_hit_rate(self):
        rets = pd.Series([0.1, -0.2, 0.05, -0.05])
        m = compute_metrics(rets, risk_free_annual=0.0)
        assert m.hit_rate == 0.5
        assert m.max_drawdown == pytest.approx(0.8778 / 1.1 - 1)

    def test_empty_is_zero(self):
        m = compute_metrics(pd.Series(dtype=float))
        assert m.cumulative_return == 0.0
        assert m.sharpe == 0.0

    def test_information_ratio_vs_benchmark(self):
        idx = pd.RangeIndex(50)
        port = pd.Series([0.002] * 50, index=idx)
        bench = pd.Series([0.001] * 50, index=idx)
        m = compute_metrics(port, risk_free_annual=0.0,
                            benchmark_returns={"SPY": bench})
        # constant spread -> std 0 -> IR stays None, but excess is positive
        assert m.benchmark_excess_return["SPY"] > 0
