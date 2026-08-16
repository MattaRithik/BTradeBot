"""TRUE walk-forward backtest: multi-period research, delta rebalancing, resume."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from quant_platform.backtest.walkforward import (
    WalkForwardError,
    rebalance_dates,
    run_walkforward,
)
from quant_platform.core.enums import SignalClass, TargetType
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import Signal, SignalPackage
from quant_platform.snapshots import freeze_snapshot

START = date(2024, 1, 2)
END = date(2024, 6, 28)


def _prices(days: int = 260) -> pd.DataFrame:
    """Business-day prices; NVDA drifts up, MU down, SPY flat."""
    sessions = pd.bdate_range("2023-06-01", periods=days + 130, tz="UTC")
    rows = []
    for i, ts in enumerate(sessions):
        rows.append({"timestamp": ts, "ticker": "NVDA", "close": 100.0 + i * 0.3, "volume": 1e7})
        rows.append({"timestamp": ts, "ticker": "MU", "close": max(5.0, 80.0 - i * 0.05), "volume": 5e6})
        rows.append({"timestamp": ts, "ticker": "SPY", "close": 500.0, "volume": 5e7})
    return pd.DataFrame(rows)


def _signals(as_of: date) -> SignalPackage:
    return SignalPackage(
        package_id=f"pkg_{as_of}",
        run_id=f"run_{as_of}",
        as_of_date=as_of,
        signals=[
            Signal(
                signal_id=f"sig_nvda_{as_of}",
                target="NVDA",
                target_type=TargetType.SECURITY,
                ticker="NVDA",
                raw_score=0.9,
                confidence=0.8,
                signal_class=SignalClass.STRONG_LONG,
                action_allowed=True,
                as_of_date=as_of,
            ),
            Signal(
                signal_id=f"sig_mu_{as_of}",
                target="MU",
                target_type=TargetType.SECURITY,
                ticker="MU",
                raw_score=0.6,
                confidence=0.6,
                signal_class=SignalClass.MODERATE_LONG,
                action_allowed=True,
                as_of_date=as_of,
            ),
        ],
    )


def _frozen_snapshot(as_of: date):
    ctx = ResearchContext(
        run_id=f"run_{as_of}", as_of_date=as_of, visible_start=date(2023, 6, 1), visible_end=as_of
    )
    return freeze_snapshot(ctx, signals=_signals(as_of))


class TestRebalanceDates:
    def test_last_session_of_each_month(self):
        sessions = sorted(pd.bdate_range(START, END).date)
        dates = rebalance_dates(sessions, START, END, "monthly")
        assert dates == sorted(dates)
        assert len(dates) == 6  # Jan..Jun 2024
        for d in dates:
            # each is the last in-window session of its month
            later_same_month = [
                s for s in sessions if s > d and (s.year, s.month) == (d.year, d.month)
            ]
            assert not later_same_month

    def test_weekly_frequency(self):
        sessions = sorted(pd.bdate_range(START, END).date)
        assert len(rebalance_dates(sessions, START, END, "weekly")) > 20

    def test_empty_window(self):
        assert rebalance_dates([date(2024, 1, 2)], date(2025, 1, 1), date(2025, 2, 1), "monthly") == []


class TestWalkForward:
    async def test_multi_period_research_and_metrics(self, tmp_path):
        calls: list[date] = []

        async def research_fn(as_of: date):
            calls.append(as_of)
            return _frozen_snapshot(as_of)

        result = await run_walkforward(
            _prices(),
            research_fn,
            start=START,
            end=END,
            rebalance="monthly",
            strategy="score_weighted",
            backtests_dir=tmp_path,
        )
        assert result.completed
        assert len(result.splits) >= 5  # research re-ran per decision date
        assert len(calls) == len(result.splits)
        assert all(s.snapshot_id for s in result.splits)
        assert any(s.turnover > 0 for s in result.splits)
        assert any(s.cost > 0 for s in result.splits)
        assert result.metrics is not None
        assert "SPY" in result.benchmark_returns
        # entry is always strictly after the decision date
        for s in result.splits:
            assert date.fromisoformat(s.entry_date) > s.as_of_date
        # artifacts persisted
        run_dir = tmp_path / result.backtest_id
        assert (run_dir / "equity.parquet").exists()
        assert (run_dir / "result.json").exists()
        assert (run_dir / "state.json").exists()
        assert any("dividends EXCLUDED" in w for w in result.warnings)
        assert any("survivorship" in w for w in result.warnings)

    async def test_resume_never_repeats_completed_splits(self, tmp_path):
        calls: list[date] = []

        async def research_fn(as_of: date):
            calls.append(as_of)
            return _frozen_snapshot(as_of)

        kwargs = dict(
            start=START, end=END, rebalance="monthly", strategy="score_weighted",
            backtests_dir=tmp_path, backtest_id="wf_resume_test",
        )
        first = await run_walkforward(_prices(), research_fn, **kwargs)
        assert first.completed
        n_calls = len(calls)

        resumed = await run_walkforward(_prices(), research_fn, **kwargs)
        assert len(calls) == n_calls  # nothing recomputed
        assert resumed.backtest_id == first.backtest_id
        assert len(resumed.splits) == len(first.splits)
        assert resumed.metrics.cumulative_return == pytest.approx(
            first.metrics.cumulative_return
        )

    async def test_checkpoint_rejects_mismatched_window(self, tmp_path):
        async def research_fn(as_of: date):
            return _frozen_snapshot(as_of)

        kwargs = dict(rebalance="monthly", strategy="cash", backtests_dir=tmp_path, backtest_id="wf_x")
        await run_walkforward(_prices(), research_fn, start=START, end=END, **kwargs)
        with pytest.raises(WalkForwardError, match="checkpoint"):
            await run_walkforward(_prices(), research_fn, start=START, end=date(2025, 1, 1), **kwargs)

    async def test_tampered_snapshot_fails(self, tmp_path):
        async def research_fn(as_of: date):
            snap = _frozen_snapshot(as_of)
            return snap.model_copy(update={"warnings": ["tampered"]})

        with pytest.raises(WalkForwardError, match="integrity"):
            await run_walkforward(
                _prices(), research_fn, start=START, end=END,
                backtests_dir=tmp_path, strategy="cash",
            )

    async def test_no_sessions_is_honest_error(self, tmp_path):
        async def research_fn(as_of: date):
            raise AssertionError("must not be called")

        empty = pd.DataFrame(columns=["timestamp", "ticker", "close"])
        with pytest.raises(WalkForwardError, match="no rebalance sessions"):
            await run_walkforward(empty, research_fn, start=START, end=END, backtests_dir=tmp_path)

    async def test_cash_strategy_is_valid_outcome(self, tmp_path):
        async def research_fn(as_of: date):
            return _frozen_snapshot(as_of)

        result = await run_walkforward(
            _prices(), research_fn, start=START, end=END,
            rebalance="monthly", strategy="cash", backtests_dir=tmp_path,
        )
        assert result.completed
        assert all(s.n_positions == 0 for s in result.splits)
        # all-cash still earns the cash yield, no trading costs
        assert result.metrics is not None
        assert all(s.cost == 0 for s in result.splits)
        assert result.metrics.cumulative_return > 0

    async def test_snapshot_loader_reuses_persisted_on_resume(self, tmp_path):
        """A resuming run loads persisted snapshots instead of re-running research."""
        calls: list[date] = []

        async def research_fn(as_of: date):
            calls.append(as_of)
            return _frozen_snapshot(as_of)

        kwargs = dict(
            start=START, end=END, rebalance="monthly", strategy="score_weighted",
            backtests_dir=tmp_path, backtest_id="wf_loader",
        )
        await run_walkforward(_prices(), research_fn, **kwargs)
        state = json.loads((tmp_path / "wf_loader" / "state.json").read_text())
        snapshot_ids = state["snapshot_ids"]
        assert snapshot_ids

        loaded: list[str] = []

        def loader(snapshot_id: str):
            loaded.append(snapshot_id)
            as_of = next(k for k, v in snapshot_ids.items() if v == snapshot_id)
            return _frozen_snapshot(date.fromisoformat(as_of))

        # wipe completed flags but keep snapshot ids -> loader path exercised
        state["splits"] = {}
        state["split_results"] = []
        state["equity"] = []
        state["holdings"] = {}
        state["cash"] = 1.0
        (tmp_path / "wf_loader" / "state.json").write_text(json.dumps(state, default=str))

        n_calls = len(calls)
        result = await run_walkforward(_prices(), research_fn, snapshot_loader=loader, **kwargs)
        assert len(calls) == n_calls  # research never re-ran
        assert len(loaded) == len(result.splits)
