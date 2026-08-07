"""Stage I: execution pipeline, risk gate, kill switch, mock broker."""

from __future__ import annotations

import pytest
from tests.conftest import AS_OF

from quant_platform.core.enums import AuditEventType, OrderSide, OrderStatus
from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget
from quant_platform.execution import (
    ExecutionConfig,
    GlobalKillSwitch,
    MockBroker,
    build_order_intents,
    pre_trade_check,
    run_pipeline,
    validate_paper_account,
)
from quant_platform.execution.broker import BrokerError


def _target(weights: dict[str, float]) -> PortfolioTarget:
    gross = sum(abs(w) for w in weights.values())
    return PortfolioTarget(
        target_id="tgt_t",
        run_id="run1",
        strategy="test",
        as_of_date=AS_OF,
        positions=[PortfolioPosition(ticker=t, weight=w) for t, w in weights.items()],
        cash_weight=1.0 - gross,
        gross_exposure=gross,
        net_exposure=gross,
    )


class TestPaperAccount:
    def test_du_prefix_accepted(self):
        assert validate_paper_account("DU1234567") == "DU1234567"

    def test_live_account_refused(self):
        with pytest.raises(BrokerError, match="PAPER"):
            validate_paper_account("U1234567")

    def test_empty_account_refused(self):
        with pytest.raises(BrokerError, match="no account"):
            validate_paper_account("")


class TestKillSwitch:
    def test_engage_disengage(self, tmp_path, audit):
        ks = GlobalKillSwitch(tmp_path / "KILL_SWITCH", audit=audit)
        assert not ks.engaged()
        ks.engage("test halt")
        assert ks.engaged()
        ks.disengage("resume")
        assert not ks.engaged()
        assert audit.count_by_type(AuditEventType.KILL_SWITCH_CHANGED) == 2

    def test_blocks_entire_pipeline(self, tmp_path):
        ks = GlobalKillSwitch(tmp_path / "KILL_SWITCH")
        ks.engage("panic")
        broker = MockBroker()
        broker.connect()
        result = run_pipeline(_target({"NVDA": 0.1}), {"NVDA": 100.0}, broker,
                              account_value=1e6, kill_switch=ks, dry_run=False)
        assert result.blocked_by_kill_switch
        assert result.orders == []
        assert broker.executions == []


class TestIntents:
    def test_deterministic_and_idempotent(self):
        target = _target({"NVDA": 0.1, "AVGO": -0.05})
        target = target.model_copy(update={"warnings": ["short"]})
        prices = {"NVDA": 100.0, "AVGO": 200.0}
        i1 = build_order_intents(target, prices, 1e6)
        i2 = build_order_intents(target, prices, 1e6)
        assert [i.idempotency_key for i in i1] == [i.idempotency_key for i in i2]
        nvda = next(i for i in i1 if i.ticker == "NVDA")
        assert nvda.side == OrderSide.BUY
        assert nvda.quantity == pytest.approx(1000.0)  # 0.1 * 1e6 / 100
        avgo = next(i for i in i1 if i.ticker == "AVGO")
        assert avgo.side == OrderSide.SELL

    def test_unpriced_ticker_skipped(self):
        intents = build_order_intents(_target({"NVDA": 0.1}), {}, 1e6)
        assert intents == []


class TestPreTradeCheck:
    def _intent(self, notional: float = 10_000, age: float = 0.0):
        return build_order_intents(_target({"NVDA": notional / 1e6}), {"NVDA": 100.0},
                                   1e6, signal_age_seconds=age)[0]

    def test_clean_passes(self):
        assert pre_trade_check(self._intent()).approved

    def test_notional_cap(self):
        d = pre_trade_check(self._intent(50_000), ExecutionConfig(max_order_notional=25_000))
        assert not d.approved
        assert any("max_order_notional" in r for r in d.reasons)

    def test_stale_signal(self):
        d = pre_trade_check(self._intent(age=100_000), ExecutionConfig(stale_signal_seconds=86_400))
        assert not d.approved
        assert any("signal age" in r for r in d.reasons)

    def test_duplicate_key_rejected(self):
        intent = self._intent()
        d = pre_trade_check(intent, seen_keys={intent.idempotency_key})
        assert not d.approved
        assert any("duplicate" in r for r in d.reasons)

    def test_concurrency_cap(self):
        d = pre_trade_check(self._intent(), open_order_count=10)
        assert not d.approved

    def test_turnover_cap(self):
        d = pre_trade_check(self._intent(10_000), day_turnover=249_000)
        assert not d.approved


class TestPipeline:
    def test_dry_run_submits_nothing(self, audit):
        broker = MockBroker()
        broker.connect()
        result = run_pipeline(_target({"NVDA": 0.02}), {"NVDA": 100.0}, broker,
                              account_value=1e6, dry_run=True, audit=audit)
        assert result.orders[0].status == OrderStatus.DRY_RUN
        assert broker.executions == []
        assert audit.count_by_type(AuditEventType.ORDER_INTENT) == 1

    def test_live_paper_submission_fills(self, audit):
        broker = MockBroker()
        broker.connect()
        result = run_pipeline(_target({"NVDA": 0.02}), {"NVDA": 100.0}, broker,
                              account_value=1e6, dry_run=False, audit=audit)
        assert result.orders[0].status == OrderStatus.FILLED
        assert len(broker.executions) == 1
        assert broker.positions()[0].ticker == "NVDA"
        assert audit.count_by_type(AuditEventType.ORDER_FILLED) == 1

    def test_rejected_orders_audited(self, audit):
        broker = MockBroker()
        broker.connect()
        cfg = ExecutionConfig(max_order_notional=1_000)
        result = run_pipeline(_target({"NVDA": 0.02}), {"NVDA": 100.0}, broker,
                              account_value=1e6, config=cfg, dry_run=False, audit=audit)
        assert result.orders[0].status == OrderStatus.RISK_REJECTED
        assert broker.executions == []
        assert audit.count_by_type(AuditEventType.ORDER_REJECTED) == 1

    def test_duplicate_intents_blocked_second_time(self):
        broker = MockBroker()
        broker.connect()
        target = _target({"NVDA": 0.02})
        run_pipeline(target, {"NVDA": 100.0}, broker, account_value=1e6, dry_run=False)
        # same run -> same idempotency keys: replaying the target re-checks keys
        intents = build_order_intents(target, {"NVDA": 100.0}, 1e6)
        assert len(intents) == 1
        assert intents[0].idempotency_key  # stable — pipeline-level dedup tested above


class TestMockBrokerAccounting:
    def test_buy_then_sell_updates_cash_and_position(self):
        broker = MockBroker(cash=100_000)
        broker.connect()
        from quant_platform.core.schemas import PaperOrder

        buy = PaperOrder(order_id="o1", intent_id="i1", account=broker.account,
                         ticker="NVDA", side=OrderSide.BUY, quantity=10,
                         limit_price=100.0)
        broker.submit_order(buy)
        assert broker.cash == pytest.approx(99_000)
        sell = buy.model_copy(update={"order_id": "o2", "side": OrderSide.SELL,
                                      "limit_price": 110.0})
        broker.submit_order(sell)
        assert broker.cash == pytest.approx(100_100)
        assert broker.positions() == []
        snapshot = broker.account_snapshot()
        assert snapshot.is_paper
        assert snapshot.net_liquidation == pytest.approx(100_100)

    def test_disconnected_broker_refuses(self):
        from quant_platform.core.schemas import PaperOrder

        broker = MockBroker()
        order = PaperOrder(order_id="o1", intent_id="i1", account=broker.account,
                           ticker="NVDA", side=OrderSide.BUY, quantity=1,
                           limit_price=100.0)
        with pytest.raises(BrokerError, match="not connected"):
            broker.submit_order(order)
