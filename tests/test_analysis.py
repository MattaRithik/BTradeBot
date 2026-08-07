"""Stage H: attribution analytics + failure records."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from tests.conftest import AS_OF, make_evidence

from quant_platform.analysis import (
    build_failure_record,
    category_performance,
    classify_failure,
    confidence_calibration,
    directional_accuracy,
    event_study,
    information_coefficient,
)
from quant_platform.core.enums import Direction, FailureType
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import (
    AgentArgument,
    BacktestMetrics,
    BacktestResult,
    PredictionSnapshot,
    WalkForwardSplit,
)
from quant_platform.snapshots import freeze_snapshot


class TestDirectionalAccuracy:
    def test_perfect_calls(self):
        dirs = pd.Series([Direction.POSITIVE, Direction.NEGATIVE])
        rets = pd.Series([0.05, -0.03])
        assert directional_accuracy(dirs, rets) == 1.0

    def test_half_right(self):
        dirs = pd.Series([Direction.POSITIVE, Direction.POSITIVE])
        rets = pd.Series([0.05, -0.03])
        assert directional_accuracy(dirs, rets) == 0.5

    def test_neutral_calls_excluded(self):
        dirs = pd.Series([Direction.NEUTRAL, Direction.NEUTRAL])
        rets = pd.Series([0.05, -0.03])
        assert directional_accuracy(dirs, rets) == 0.0


class TestIC:
    def test_perfect_monotonic(self):
        scores = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5])
        rets = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        ic = information_coefficient(scores, rets)
        assert ic["pearson"] == pytest.approx(1.0)
        assert ic["spearman"] == pytest.approx(1.0)

    def test_inverted_is_negative(self):
        scores = pd.Series([0.5, 0.4, 0.3, 0.2, 0.1])
        rets = pd.Series([0.01, 0.02, 0.03, 0.04, 0.05])
        assert information_coefficient(scores, rets)["spearman"] == pytest.approx(-1.0)

    def test_too_few_pairs_is_nan(self):
        ic = information_coefficient(pd.Series([0.1, 0.2]), pd.Series([0.01, 0.02]))
        assert np.isnan(ic["pearson"])


class TestEventStudy:
    def test_uptrend_positive_forward_returns(self):
        days = pd.bdate_range("2024-01-01", periods=100, tz="UTC")
        prices = pd.DataFrame({"close": 100 * 1.001 ** np.arange(100)}, index=days)
        out = event_study(prices, [days[10], days[20]], horizons=(5, 21, 42))
        row5 = out[out["horizon_days"] == 5].iloc[0]
        assert row5["n_events"] == 2
        assert row5["mean_forward_return"] == pytest.approx(1.001**5 - 1, abs=1e-9)
        row42 = out[out["horizon_days"] == 42].iloc[0]
        assert row42["n_events"] == 2

    def test_events_near_end_only_reach_short_horizons(self):
        days = pd.bdate_range("2024-01-01", periods=30, tz="UTC")
        prices = pd.DataFrame({"close": 100.0}, index=days)
        out = event_study(prices, [days[28]], horizons=(5, 21))
        assert out[out["horizon_days"] == 5]["n_events"].iloc[0] == 0
        assert out[out["horizon_days"] == 21]["n_events"].iloc[0] == 0


class TestCategoryPerformance:
    def test_groups_by_category(self):
        cards = [make_evidence("e1")]  # DEMAND_SIGNAL on NVDA
        out = category_performance(cards, {"NVDA": 0.10})
        assert len(out) == 1
        assert out.iloc[0]["category"] == "demand_signal"
        assert out.iloc[0]["mean_forward_return"] == pytest.approx(0.10)


class TestCalibration:
    def test_perfect_calibration(self):
        conf = pd.Series([0.9, 0.9, 0.1, 0.1])
        correct = pd.Series([True, True, False, False])
        out = confidence_calibration(conf, correct, n_buckets=2)
        high = out[out["mean_confidence"] > 0.5].iloc[0]
        assert high["hit_rate"] == 1.0

    def test_empty(self):
        out = confidence_calibration(pd.Series(dtype=float), pd.Series(dtype=bool))
        assert out.empty


def _result(cum: float, max_dd: float = -0.05, costs: float = 0.001) -> BacktestResult:
    return BacktestResult(
        result_id="bt_x",
        snapshot_id="snap_x",
        split=WalkForwardSplit(
            split_id="s1",
            lookback_start=date(2023, 1, 1),
            as_of_date=AS_OF,
            test_start=date(2025, 1, 1),
            test_end=date(2025, 2, 28),
        ),
        metrics=BacktestMetrics(cumulative_return=cum, max_drawdown=max_dd,
                                transaction_costs=costs, turnover=1.0),
    )


def _snapshot() -> PredictionSnapshot:
    ctx = ResearchContext(
        run_id="run1", as_of_date=AS_OF, visible_start=date(2023, 1, 1),
        visible_end=AS_OF, test_start=date(2025, 1, 1), test_end=date(2025, 2, 28),
    )
    return freeze_snapshot(ctx, evidence_ids=["e1"])


class TestClassification:
    def test_deep_drawdown_is_risk_limit(self):
        assert classify_failure(_result(0.05, max_dd=-0.25)) == FailureType.RISK_LIMIT

    def test_cost_drag_is_execution(self):
        assert classify_failure(_result(-0.01, costs=0.05)) == FailureType.EXECUTION_SLIPPAGE

    def test_negative_vs_positive_benchmark_is_thesis_wrong(self):
        assert classify_failure(_result(-0.05), benchmark_cum=0.10) == FailureType.THESIS_WRONG

    def test_positive_but_behind_benchmark(self):
        assert (classify_failure(_result(0.03), benchmark_cum=0.10)
                == FailureType.BENCHMARK_UNDERPERFORMANCE)

    def test_losing_alone_is_timing(self):
        assert classify_failure(_result(-0.05), benchmark_cum=-0.10) == FailureType.TIMING_WRONG


class TestFailureRecord:
    def test_built_without_mutating_snapshot(self):
        snap = _snapshot()
        before = snap.model_dump_json()
        narrative = AgentArgument(
            agent_name="failure",
            conclusion="demand signal was stale",
            confidence=0.7,
            direction=Direction.NEGATIVE,
            risks=["rely less on single-source news"],
            as_of_date=AS_OF,
            details={"suggested_improvement": "require 2-source confirmation"},
        )
        record = build_failure_record(snap, _result(-0.05), narrative=narrative,
                                      benchmark_cum=0.1)
        assert record.failure_type == FailureType.THESIS_WRONG
        assert record.failed_component == "thesis"
        assert record.evidence_ids == ["e1"]
        assert "stale" in record.what_happened
        assert record.suggested_improvement == "require 2-source confirmation"
        assert snap.model_dump_json() == before  # snapshot untouched

    def test_deterministic_id(self):
        snap = _snapshot()
        r1 = build_failure_record(snap, _result(-0.05))
        r2 = build_failure_record(snap, _result(-0.05))
        assert r1.failure_id == r2.failure_id
