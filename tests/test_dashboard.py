"""Stage J: dashboard loaders (streamlit-free, artifact-only)."""

from __future__ import annotations

from datetime import date

import pytest

from quant_platform.core.config import EnvSettings
from quant_platform.core.enums import SignalClass, TargetType, ValidationStatus
from quant_platform.core.gatekeeper import ResearchContext
from quant_platform.core.schemas import (
    RankedSector,
    RankingResult,
    Signal,
    SignalPackage,
)
from quant_platform.dashboard import (
    assert_no_sector_tickers,
    load_audit,
    load_rankings,
    load_snapshots,
    signals_frame,
    system_status,
)
from quant_platform.snapshots import freeze_snapshot


def _snapshot_with_artifacts(store) -> None:
    ctx = ResearchContext(
        run_id="run1", as_of_date=date(2024, 12, 31), visible_start=date(2023, 1, 1),
        visible_end=date(2024, 12, 31), test_start=date(2025, 1, 1),
        test_end=date(2025, 2, 28),
    )
    ranking = RankingResult(
        run_id="run1",
        as_of_date=date(2024, 12, 31),
        leaderboard=[
            RankedSector(rank=1, sector="AI Infrastructure", composite_score=0.8,
                         validation_status=ValidationStatus.APPROVED, selected=True)
        ],
        selection_rationale="1 sector(s) selected: AI Infrastructure",
    )
    signals = SignalPackage(
        package_id="pkg1",
        run_id="run1",
        as_of_date=date(2024, 12, 31),
        signals=[
            Signal(signal_id="s_sector", target="AI Infrastructure",
                   target_type=TargetType.SECTOR, raw_score=0.8, confidence=0.8,
                   signal_class=SignalClass.STRONG_LONG, action_allowed=False,
                   as_of_date=date(2024, 12, 31)),
            Signal(signal_id="s_nvda", target="NVDA", target_type=TargetType.SECURITY,
                   ticker="NVDA", raw_score=0.8, confidence=0.8,
                   signal_class=SignalClass.STRONG_LONG, action_allowed=True,
                   as_of_date=date(2024, 12, 31)),
        ],
    )
    freeze_snapshot(ctx, ranking=ranking, signals=signals, store=store)


class TestLoaders:
    def test_snapshots_and_rankings(self, store):
        _snapshot_with_artifacts(store)
        snaps = load_snapshots(store)
        assert len(snaps) == 1
        rankings = load_rankings(store)
        assert rankings[0].leaderboard[0].sector == "AI Infrastructure"

    def test_empty_store(self, store):
        assert load_snapshots(store) == []
        assert load_rankings(store) == []

    def test_audit_frame(self, audit):
        from quant_platform.core.enums import AuditEventType

        audit.record(AuditEventType.CONFIG_LOADED, run_id="r")
        df = load_audit(audit.path)
        assert len(df) == 1
        assert df.iloc[0]["event"] == "CONFIG_LOADED"
        assert list(load_audit("/nonexistent/audit.jsonl").columns) == [
            "ts", "event", "run_id", "as_of_date", "details"
        ]


class TestSystemStatus:
    def test_honest_offline_status(self, monkeypatch):
        for var in ("KIMI_API_KEY", "IBKR_ACCOUNT"):
            monkeypatch.delenv(var, raising=False)
        status = system_status(EnvSettings.from_env())
        assert status["trading_mode"] == "paper"
        assert status["dry_run"] is True
        assert status["kimi_configured"] is False
        assert status["bloomberg_blpapi"] is False  # expected off-terminal
        assert status["ibkr_client"] is False


class TestSectorLabelGuard:
    def test_sector_with_ticker_fails_loudly(self):
        with pytest.raises(ValueError, match="never tradable"):
            assert_no_sector_tickers(
                [{"target": "AI Infrastructure", "target_type": "sector", "ticker": "NVDA"}]
            )

    def test_security_rows_pass(self):
        assert_no_sector_tickers(
            [{"target": "NVDA", "target_type": "security", "ticker": "NVDA"}]
        )

    def test_signals_frame_keeps_labels_clean(self, store):
        _snapshot_with_artifacts(store)
        pkg = load_snapshots(store)[0].signals
        df = signals_frame(pkg)
        sector_row = df[df["target_type"] == "sector"].iloc[0]
        import pandas as pd

        assert pd.isna(sector_row["ticker"])  # labels never carry a ticker
        assert sector_row["action_allowed"] == False  # noqa: E712 — numpy bool in frames


class TestNewArtifactViews:
    def test_walkforward_results_and_equity(self, store):
        import pandas as pd

        from quant_platform.core.schemas import (
            BacktestMetrics,
            WalkForwardResult,
            WalkForwardSplitResult,
        )
        from quant_platform.dashboard import load_equity_curve, load_walkforward_results

        run_dir = store.dir("backtests") / "wf_test"
        run_dir.mkdir(parents=True, exist_ok=True)
        result = WalkForwardResult(
            backtest_id="wf_test", start=date(2024, 1, 2), end=date(2024, 6, 28),
            rebalance="monthly", strategy="ensemble",
            splits=[WalkForwardSplitResult(
                as_of_date=date(2024, 1, 31), snapshot_id="snap_x",
                entry_date="2024-02-01", exit_date="2024-02-29",
                segment_return=0.01, turnover=0.5, cost=0.001, n_positions=3,
            )],
            metrics=BacktestMetrics(cumulative_return=0.05),
            created_at="2024-07-01T00:00:00+00:00", completed=True,
        )
        (run_dir / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
        pd.DataFrame({"date": ["2024-02-01", "2024-02-02"], "equity": [1.0, 1.01]}).to_parquet(
            run_dir / "equity.parquet", index=False
        )

        loaded = load_walkforward_results(store)
        assert [r.backtest_id for r in loaded] == ["wf_test"]
        curve = load_equity_curve(store, "wf_test")
        assert list(curve["equity"]) == [1.0, 1.01]
        assert load_walkforward_results(store)[0].splits[0].snapshot_id == "snap_x"

    def test_evaluations_view(self, store):
        from quant_platform.core.schemas import HorizonPerformance, SnapshotEvaluation
        from quant_platform.dashboard import load_evaluations

        assert load_evaluations(store) == []
        ev = SnapshotEvaluation(
            evaluation_id="ev1", snapshot_id="snap_x", run_id="r1",
            as_of_date=date(2025, 1, 31), visible_cutoff="2025-01-31T21:15:00+00:00",
            entry_date="2025-02-03",
            horizons=[HorizonPerformance(horizon="1M", end_date="2025-02-28",
                                         portfolio_return=0.03,
                                         benchmark_returns={"SPY": 0.01})],
            created_at="2025-03-01T00:00:00+00:00",
        )
        store.save_model("evaluations", ev.evaluation_id, ev)
        loaded = load_evaluations(store)
        assert loaded[0].horizons[0].benchmark_returns["SPY"] == 0.01

    def test_paper_ledger_and_kill_switch(self, store):
        from quant_platform.dashboard import (
            kill_switch_engaged,
            load_paper_ledger,
            load_reconciliations,
        )
        from quant_platform.execution import OrderLedger

        assert load_paper_ledger(store).empty
        assert not kill_switch_engaged(store)

        ledger = OrderLedger(store.root / "paper_trading" / "ledger.jsonl")
        from quant_platform.core.enums import OrderStatus

        ledger.record(
            idempotency_key="k1", intent_id="i1", order_id="o1", ticker="NVDA",
            side="BUY", quantity=10.0, as_of_date="2025-01-31",
            status=OrderStatus.FILLED, broker_order_id="42",
        )
        df = load_paper_ledger(store)
        assert len(df) == 1 and df.iloc[0]["ticker"] == "NVDA"

        (store.root / "paper_trading" / "KILL_SWITCH").write_text("engaged: test\n")
        assert kill_switch_engaged(store)
        assert load_reconciliations(store) == []
