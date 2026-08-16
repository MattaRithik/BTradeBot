"""Paper execution: delta orders, persistent idempotency, all risk limits,
honest order states, reconciliation. PAPER ONLY."""

from __future__ import annotations

import pytest
from tests.conftest import AS_OF

from quant_platform.core.config import EnvSettings
from quant_platform.core.enums import OrderSide, OrderStatus
from quant_platform.core.schemas import (
    PaperAccountSnapshot,
    PaperOrder,
    PaperPosition,
    PortfolioPosition,
    PortfolioTarget,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.execution import (
    MockBroker,
    OrderLedger,
    build_delta_intents,
    pre_trade_check,
    reconcile_positions,
    run_pipeline,
)
from quant_platform.execution.broker import BrokerError, SubmitResult
from quant_platform.execution.ibkr_paper import IBKRPaperBroker


def _target(weights: dict[str, float], run_id: str = "run1") -> PortfolioTarget:
    gross = sum(abs(w) for w in weights.values())
    return PortfolioTarget(
        target_id="tgt_t",
        run_id=run_id,
        strategy="test",
        as_of_date=AS_OF,
        positions=[PortfolioPosition(ticker=t, weight=w) for t, w in weights.items()],
        cash_weight=1.0 - gross,
        gross_exposure=gross,
        net_exposure=gross,
    )


def _pos(ticker: str, qty: float, px: float = 100.0) -> PaperPosition:
    return PaperPosition(account="DU1", ticker=ticker, quantity=qty, avg_cost=px)


class TestDeltaIntents:
    def test_buys_only_shortfall(self):
        # hold 500 NVDA @100 = 50k; target 0.1 of 1e6 = 100k -> buy 50k delta
        intents, warnings = build_delta_intents(
            _target({"NVDA": 0.1}), {"NVDA": 100.0}, [_pos("NVDA", 500)], 1e6
        )
        assert warnings == []
        assert len(intents) == 1
        assert intents[0].side == OrderSide.BUY
        assert intents[0].notional_estimate == pytest.approx(50_000)
        assert intents[0].quantity == pytest.approx(500)

    def test_sells_excess_and_liquidates_removed(self):
        intents, _ = build_delta_intents(
            _target({"NVDA": 0.05}), {"NVDA": 100.0, "MU": 50.0},
            [_pos("NVDA", 1000), _pos("MU", 400, 50.0)], 1e6,
        )
        by_ticker = {i.ticker: i for i in intents}
        assert by_ticker["NVDA"].side == OrderSide.SELL  # 100k -> 50k
        assert by_ticker["NVDA"].notional_estimate == pytest.approx(50_000)
        assert by_ticker["MU"].side == OrderSide.SELL  # not in target -> liquidate
        assert by_ticker["MU"].quantity == pytest.approx(400)

    def test_at_target_produces_no_orders(self):
        intents, _ = build_delta_intents(
            _target({"NVDA": 0.1}), {"NVDA": 100.0}, [_pos("NVDA", 1000)], 1e6
        )
        assert intents == []

    def test_unpriced_held_ticker_warns_not_trades(self):
        intents, warnings = build_delta_intents(
            _target({"NVDA": 0.1}), {"NVDA": 100.0}, [_pos("NVDA", 500), _pos("MU", 10)], 1e6
        )
        assert [i.ticker for i in intents] == ["NVDA"]
        assert any("MU" in w for w in warnings)

    def test_idempotency_key_has_no_run_id(self):
        t1 = _target({"NVDA": 0.1}, run_id="run_a")
        t2 = _target({"NVDA": 0.1}, run_id="run_b")
        i1, _ = build_delta_intents(t1, {"NVDA": 100.0}, [], 1e6)
        i2, _ = build_delta_intents(t2, {"NVDA": 100.0}, [], 1e6)
        assert i1[0].idempotency_key == i2[0].idempotency_key


class TestAllConfiguredLimitsEnforced:
    def _intent(self, notional: float = 10_000):
        intents, _ = build_delta_intents(
            _target({"NVDA": notional / 1e6}), {"NVDA": 100.0}, [], 1e6
        )
        return intents[0]

    def test_resulting_position_cap(self):
        d = pre_trade_check(self._intent(), resulting_position_notional=150_000)
        assert not d.approved
        assert any("max_position_notional" in r for r in d.reasons)

    def test_resulting_gross_cap(self):
        d = pre_trade_check(self._intent(), resulting_portfolio_gross=1_500_000)
        assert not d.approved
        assert any("max_portfolio_gross" in r for r in d.reasons)

    def test_daily_loss_cap(self):
        d = pre_trade_check(self._intent(), day_pnl=-60_000)
        assert not d.approved
        assert any("max_daily_loss" in r for r in d.reasons)

    def test_daily_loss_unknown_is_not_faked(self):
        # day_pnl=None -> limit honestly unenforceable, order may still pass
        assert pre_trade_check(self._intent(), day_pnl=None).approved

    def test_stale_price_rejected(self):
        d = pre_trade_check(self._intent(), price_age_seconds=5_000)
        assert not d.approved
        assert any("price age" in r for r in d.reasons)


class TestPersistentLedger:
    def test_restart_cannot_duplicate(self, tmp_path):
        ledger_path = tmp_path / "ledger.jsonl"
        broker = MockBroker()
        broker.connect()
        target = _target({"NVDA": 0.02})
        first = run_pipeline(
            target, {"NVDA": 100.0}, broker, account_value=1e6,
            dry_run=False, ledger=OrderLedger(ledger_path),
        )
        assert first.orders[0].status == OrderStatus.FILLED

        # simulate a process restart: brand-new ledger object on the same file,
        # and positions already at target so only the ledger could catch a retry
        fresh_broker = MockBroker()
        fresh_broker.connect()
        second = run_pipeline(
            target, {"NVDA": 100.0}, fresh_broker, account_value=1e6,
            dry_run=False, ledger=OrderLedger(ledger_path),
        )
        assert second.orders[0].status == OrderStatus.RISK_REJECTED
        assert any("duplicate" in r for r in second.decisions[0].reasons)
        assert fresh_broker.executions == []

    def test_dry_run_never_writes_ledger(self, tmp_path):
        broker = MockBroker()
        broker.connect()
        run_pipeline(_target({"NVDA": 0.02}), {"NVDA": 100.0}, broker,
                     account_value=1e6, dry_run=True, ledger=OrderLedger(tmp_path / "l.jsonl"))
        assert not (tmp_path / "l.jsonl").exists()


class TestHonestOrderStates:
    def _broker_returning(self, status: OrderStatus, with_fill: bool = False):
        class _Broker(MockBroker):
            def submit_and_monitor(self, order: PaperOrder, timeout_seconds: float = 30.0):
                execution = None
                if with_fill:
                    execution = super().submit_order(order)
                return SubmitResult(
                    order=order.model_copy(update={"status": status}),
                    execution=execution,
                )

        broker = _Broker()
        broker.connect()
        return broker

    def test_partial_fill_recorded_not_faked(self):
        broker = self._broker_returning(OrderStatus.PARTIALLY_FILLED, with_fill=True)
        result = run_pipeline(
            _target({"NVDA": 0.02}), {"NVDA": 100.0}, broker, account_value=1e6, dry_run=False
        )
        assert result.orders[0].status == OrderStatus.PARTIALLY_FILLED
        assert any("PARTIALLY_FILLED" in w for w in result.warnings)

    def test_cancelled_and_rejected_have_no_execution(self):
        for status in (OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.SUBMITTED):
            broker = self._broker_returning(status)
            result = run_pipeline(
                _target({"NVDA": 0.02}), {"NVDA": 100.0}, broker, account_value=1e6, dry_run=False
            )
            assert result.orders[0].status == status
            assert result.executions == []

    def test_broker_exception_becomes_rejected_order(self):
        class _Down(MockBroker):
            def submit_and_monitor(self, order, timeout_seconds=30.0):
                raise BrokerError("TWS disconnected")

        broker = _Down()
        broker.connect()
        result = run_pipeline(
            _target({"NVDA": 0.02}), {"NVDA": 100.0}, broker, account_value=1e6, dry_run=False
        )
        assert result.orders[0].status == OrderStatus.REJECTED
        assert any("TWS disconnected" in w for w in result.warnings)


class TestLivePortGuard:
    def test_known_live_ports_rejected(self, monkeypatch):
        for port in (7496, 4001):
            settings = EnvSettings(ibkr_port=port, ibkr_account="DU1234567")
            with pytest.raises(BrokerError, match="LIVE"):
                IBKRPaperBroker(settings)

    def test_paper_port_passes_port_guard(self):
        settings = EnvSettings(ibkr_port=7497, ibkr_account="DU1234567")
        with pytest.raises(BrokerError, match="ib_async"):  # offline: no client lib
            IBKRPaperBroker(settings)


class TestReconcile:
    def _account(self, positions: list[PaperPosition], cash: float = 0.0):
        gross = sum(abs(p.quantity * p.avg_cost) for p in positions)
        return PaperAccountSnapshot(
            account="DU1", is_paper=True, net_liquidation=gross + cash, cash=cash,
            gross_exposure=gross, positions=positions, captured_at=utc_now(),
        )

    def test_matching_positions_reconcile(self):
        account = self._account([_pos("NVDA", 100)], cash=900_000)  # 10k + 900k
        report = reconcile_positions(
            _target({"NVDA": 0.01}), account, {"NVDA": 100.0}
        )
        # target = 1% of 910k = 9.1k vs current 10k -> within 1% tolerance
        assert report.reconciled

    def test_mismatch_reported(self):
        account = self._account([_pos("NVDA", 500)], cash=500_000)  # 50k + 500k
        report = reconcile_positions(_target({"NVDA": 0.5}), account, {"NVDA": 100.0})
        assert not report.reconciled
        assert report.discrepancies[0].ticker == "NVDA"
        assert report.discrepancies[0].delta_value == pytest.approx(225_000, rel=0.01)

    def test_unmatched_and_missing_positions(self):
        account = self._account([_pos("MU", 100, 50.0)], cash=995_000)
        report = reconcile_positions(_target({"NVDA": 0.005}), account,
                                     {"NVDA": 100.0, "MU": 50.0})
        assert report.unmatched_positions == ["MU"]
        assert report.missing_positions == ["NVDA"]
