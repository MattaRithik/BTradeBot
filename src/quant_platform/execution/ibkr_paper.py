"""IBKR paper broker adapter (ib_async, optional import).

Offline this module is import-safe and honest: constructing IBKRPaperBroker
without ib_async raises a clear error pointing at MockBroker. When ib_async
IS present (college machine with TWS/Gateway on a paper port), the adapter
connects, validates the DU* paper-account prefix, and submits market/limit
orders. Live accounts and live ports are refused by construction.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from quant_platform.core.config import EnvSettings, load_yaml_config
from quant_platform.core.schemas import (
    PaperAccountSnapshot,
    PaperExecution,
    PaperOrder,
    PaperPosition,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.execution.broker import BrokerAdapter, BrokerError, validate_paper_account

IB_ASYNC_AVAILABLE = importlib.util.find_spec("ib_async") is not None


class IBKRPaperBroker(BrokerAdapter):
    """IBKR paper trading via TWS/IB Gateway paper ports (7497/4002)."""

    name = "ibkr_paper"

    def __init__(self, settings: EnvSettings | None = None, ib: Any = None) -> None:
        self.settings = settings or EnvSettings.from_env()
        cfg = load_yaml_config("ibkr")
        if str(cfg.get("trading_mode", "paper")).lower() != "paper":
            raise BrokerError("configs/ibkr.yaml trading_mode must be 'paper'")
        self.timeout = float(cfg.get("connect_timeout_seconds", 10))
        self.account = validate_paper_account(
            self.settings.ibkr_account,
            load_yaml_config("risk")
            .get("execution", {})
            .get("require_paper_account_prefix", "DU"),
        )
        if ib is not None:
            self._ib = ib  # injected for contract tests
        elif IB_ASYNC_AVAILABLE:
            from ib_async import IB

            self._ib = IB()
        else:
            raise BrokerError(
                "ib_async is not installed — the IBKR adapter is unavailable. "
                "Install the 'ibkr' extra on the college machine and run TWS/"
                "Gateway on a PAPER port. Use MockBroker offline."
            )

    def connect(self) -> None:
        self._ib.connect(
            self.settings.ibkr_host,
            self.settings.ibkr_port,
            clientId=self.settings.ibkr_client_id,
            timeout=self.timeout,
        )
        accounts = list(self._ib.managedAccounts())
        if self.account not in accounts:
            raise BrokerError(
                f"configured paper account {self.account!r} not among managed "
                f"accounts {accounts} — refusing to trade"
            )

    def account_snapshot(self) -> PaperAccountSnapshot:
        summary = {v.tag: v for v in self._ib.accountSummary(self.account)}
        positions = [
            PaperPosition(
                account=self.account,
                ticker=p.contract.symbol,
                quantity=float(p.position),
                avg_cost=float(p.avgCost),
            )
            for p in self._ib.positions(self.account)
        ]
        return PaperAccountSnapshot(
            account=self.account,
            is_paper=True,
            net_liquidation=float(summary.get("NetLiquidation", 0).value or 0),
            cash=float(summary.get("TotalCashValue", 0).value or 0)
            if "TotalCashValue" in summary
            else 0.0,
            gross_exposure=sum(abs(p.quantity * p.avg_cost) for p in positions),
            positions=positions,
            captured_at=utc_now(),
        )

    def submit_order(self, order: PaperOrder) -> PaperExecution:
        from ib_async import LimitOrder, MarketOrder, Stock

        contract = Stock(order.ticker, "SMART", "USD")
        self._ib.qualifyContracts(contract)
        if order.order_type == "LMT":
            if order.limit_price is None:
                raise BrokerError("LMT order requires limit_price")
            ib_order = LimitOrder(order.side.value, order.quantity, order.limit_price)
        else:
            ib_order = MarketOrder(order.side.value, order.quantity)
        trade = self._ib.placeOrder(contract, ib_order)
        self._ib.sleep(1)  # brief wait for the fill on paper
        if not trade.isDone():
            raise BrokerError(
                f"order for {order.ticker} not filled promptly (status "
                f"{trade.orderStatus.status}) — recorded honestly, not faked"
            )
        fill = trade.fills[-1].execution
        return PaperExecution(
            execution_id=str(fill.execId),
            order_id=order.order_id,
            account=self.account,
            ticker=order.ticker,
            side=order.side,
            quantity=float(fill.shares),
            price=float(fill.price),
            executed_at=utc_now(),
        )

    def positions(self) -> list[PaperPosition]:
        return [
            PaperPosition(
                account=self.account,
                ticker=p.contract.symbol,
                quantity=float(p.position),
                avg_cost=float(p.avgCost),
            )
            for p in self._ib.positions(self.account)
        ]

    def disconnect(self) -> None:
        self._ib.disconnect()
