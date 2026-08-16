"""IBKR paper broker adapter (ib_async, optional import).

Offline this module is import-safe and honest: constructing IBKRPaperBroker
without ib_async raises a clear error pointing at MockBroker. When ib_async
IS present (college machine with a LOGGED-IN PAPER TWS/IB Gateway session —
the TWS API has no simple API key; it talks to the local session over
host/port/client-id), the adapter connects, validates the DU* paper-account
prefix, refuses known live ports, and submits market/limit orders with real
status monitoring (submitted/partial/filled/cancelled/rejected). Live
accounts and live ports are refused by construction.
"""

from __future__ import annotations

import importlib.util
import time
from typing import Any

from quant_platform.core.config import EnvSettings, load_yaml_config
from quant_platform.core.enums import OrderStatus
from quant_platform.core.schemas import (
    PaperAccountSnapshot,
    PaperExecution,
    PaperOrder,
    PaperPosition,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.execution.broker import (
    BrokerAdapter,
    BrokerError,
    SubmitResult,
    validate_paper_account,
)

IB_ASYNC_AVAILABLE = importlib.util.find_spec("ib_async") is not None

# TWS/IB Gateway socket ports. Paper sessions listen on 7497/4002; the known
# LIVE ports are refused unconditionally — there is no paper-safe reason to
# point this adapter at them.
PAPER_PORTS = frozenset({7497, 4002})
LIVE_PORTS = frozenset({7496, 4001})

_TERMINAL_CANCEL = {"Cancelled", "ApiCancelled", "PendingCancel", "Inactive"}
_TERMINAL_REJECT = {"ApiRejected", "Rejected"}


class IBKRPaperBroker(BrokerAdapter):
    """IBKR paper trading via TWS/IB Gateway PAPER ports (7497/4002)."""

    name = "ibkr_paper"

    def __init__(self, settings: EnvSettings | None = None, ib: Any = None) -> None:
        self.settings = settings or EnvSettings.from_env()
        cfg = load_yaml_config("ibkr")
        if str(cfg.get("trading_mode", "paper")).lower() != "paper":
            raise BrokerError("configs/ibkr.yaml trading_mode must be 'paper'")
        if self.settings.ibkr_port in LIVE_PORTS:
            raise BrokerError(
                f"IBKR_PORT={self.settings.ibkr_port} is a known LIVE TWS/Gateway port — "
                "this system trades PAPER only (paper ports: 7497 TWS, 4002 Gateway)"
            )
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

    def ensure_connected(self) -> None:
        """Reconnect once after a TWS disconnect; never silently trade offline."""
        is_connected = getattr(self._ib, "isConnected", None)
        if callable(is_connected) and is_connected():
            return
        self.connect()

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

        def _val(tag: str) -> float | None:
            item = summary.get(tag)
            if item is None or item.value in (None, ""):
                return None
            try:
                return float(item.value)
            except (TypeError, ValueError):
                return None

        net_liq = _val("NetLiquidation") or 0.0
        return PaperAccountSnapshot(
            account=self.account,
            is_paper=True,
            net_liquidation=net_liq,
            cash=_val("TotalCashValue") or 0.0,
            gross_exposure=sum(abs(p.quantity * p.avg_cost) for p in positions),
            positions=positions,
            day_pnl=_val("DailyPnL"),  # absent tag -> None -> limit honestly unenforceable
            captured_at=utc_now(),
        )

    def submit_order(self, order: PaperOrder) -> PaperExecution:
        """Immediate-fill style submit retained for the abstract contract.

        Production submission goes through submit_and_monitor, which never
        assumes a quick fill. This path raises unless the order fills at once.
        """
        result = self.submit_and_monitor(order, timeout_seconds=5.0)
        if result.execution is None or result.order.status is not OrderStatus.FILLED:
            raise BrokerError(
                f"order for {order.ticker} not filled promptly (status "
                f"{result.order.status.value}) — recorded honestly, not faked"
            )
        return result.execution

    def submit_and_monitor(
        self, order: PaperOrder, timeout_seconds: float = 30.0
    ) -> SubmitResult:
        """Submit and monitor to a terminal status — never fake a fill.

        Maps real TWS states: Filled→FILLED (+execution), partial at
        timeout→PARTIALLY_FILLED (+execution for the filled shares),
        Cancelled/Inactive→CANCELLED, Rejected→REJECTED, still working at
        timeout→stays SUBMITTED (a stale open order the reconcile step must
        resolve — it is NOT cancelled implicitly).
        """
        from ib_async import LimitOrder, MarketOrder, Stock

        self.ensure_connected()
        contract = Stock(order.ticker, "SMART", "USD")
        self._ib.qualifyContracts(contract)
        if order.order_type == "LMT":
            if order.limit_price is None:
                raise BrokerError("LMT order requires limit_price")
            ib_order = LimitOrder(order.side.value, order.quantity, order.limit_price)
        else:
            ib_order = MarketOrder(order.side.value, order.quantity)
        trade = self._ib.placeOrder(contract, ib_order)
        broker_order_id = str(trade.order.orderId)
        working = order.model_copy(
            update={
                "status": OrderStatus.SUBMITTED,
                "broker_order_id": broker_order_id,
                "submitted_at": utc_now(),
            }
        )

        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not trade.isDone() and time.monotonic() < deadline:
            self._ib.sleep(0.5)

        status_text = str(trade.orderStatus.status)
        filled_qty = float(trade.orderStatus.filled or 0.0)
        avg_price = float(trade.orderStatus.avgFillPrice or 0.0)

        execution: PaperExecution | None = None
        if filled_qty > 0 and avg_price > 0:
            execution = PaperExecution(
                execution_id=str(trade.fills[-1].execution.execId)
                if trade.fills
                else f"ibkr_{broker_order_id}",
                order_id=order.order_id,
                account=self.account,
                ticker=order.ticker,
                side=order.side,
                quantity=filled_qty,
                price=avg_price,
                executed_at=utc_now(),
            )

        if status_text == "Filled":
            status = OrderStatus.FILLED
        elif status_text in _TERMINAL_CANCEL:
            status = OrderStatus.CANCELLED
        elif status_text in _TERMINAL_REJECT:
            status = OrderStatus.REJECTED
        elif filled_qty > 0:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.SUBMITTED  # stale open order — honest, unresolved
        return SubmitResult(
            order=working.model_copy(
                update={"status": status, "updated_at": utc_now()}
            ),
            execution=execution,
        )

    def open_orders(self) -> list[Any]:
        """Raw open trades for reconciliation (ib_async objects, if connected)."""
        try:
            return list(self._ib.openTrades())
        except Exception:
            return []

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
