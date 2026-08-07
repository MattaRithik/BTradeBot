"""Broker adapter contract + MockBroker. PAPER ONLY.

No adapter in this codebase can route to a live account: the execution layer
validates the paper account prefix before any submission, and the only real
adapter (IBKRPaperBroker) connects to IBKR paper ports. MockBroker fills
deterministically at the intent's reference price so tests and the demo run
fully offline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from quant_platform.core.enums import OrderStatus
from quant_platform.core.schemas import (
    PaperAccountSnapshot,
    PaperExecution,
    PaperOrder,
    PaperPosition,
)
from quant_platform.core.timeutil import utc_now


class BrokerError(RuntimeError):
    """Honest broker failure — never fake a fill."""


class BrokerAdapter(ABC):
    """Paper broker contract. All implementations are paper-only."""

    name: str

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def account_snapshot(self) -> PaperAccountSnapshot: ...

    @abstractmethod
    def submit_order(self, order: PaperOrder) -> PaperExecution:
        """Submit and return the resulting execution (mock fills immediately)."""

    @abstractmethod
    def positions(self) -> list[PaperPosition]: ...

    def disconnect(self) -> None:  # optional
        return None


def validate_paper_account(account: str, required_prefix: str = "DU") -> str:
    """IBKR paper accounts start with DU. Anything else is refused."""
    if not account:
        raise BrokerError("no account configured — refusing to trade")
    if not account.startswith(required_prefix):
        raise BrokerError(
            f"account {account!r} does not start with {required_prefix!r} — "
            "this system trades PAPER accounts ONLY; refusing"
        )
    return account


class MockBroker(BrokerAdapter):
    """In-memory paper broker: immediate fills at the order's limit/reference
    price, running cash/positions, DU-paper account."""

    name = "mock"

    def __init__(self, account: str = "DU1234567", cash: float = 1_000_000) -> None:
        self.account = validate_paper_account(account)
        self.cash = cash
        self._positions: dict[str, PaperPosition] = {}
        self.executions: list[PaperExecution] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def account_snapshot(self) -> PaperAccountSnapshot:
        positions = list(self._positions.values())
        gross = sum(abs(p.market_value or 0.0) for p in positions)
        return PaperAccountSnapshot(
            account=self.account,
            is_paper=True,
            net_liquidation=self.cash + gross,
            cash=self.cash,
            gross_exposure=gross,
            positions=positions,
            open_orders=0,
            captured_at=utc_now(),
        )

    def submit_order(self, order: PaperOrder) -> PaperExecution:
        if not self.connected:
            raise BrokerError("MockBroker is not connected")
        price = order.limit_price if order.limit_price is not None else None
        if price is None:
            raise BrokerError(
                f"MockBroker needs a price for {order.ticker} — the pipeline must "
                "convert intents to priced orders first"
            )
        signed_qty = order.quantity if order.side.value == "BUY" else -order.quantity
        cost = signed_qty * price
        self.cash -= cost
        prev = self._positions.get(order.ticker)
        new_qty = (prev.quantity if prev else 0.0) + signed_qty
        if abs(new_qty) < 1e-12:
            self._positions.pop(order.ticker, None)
        else:
            self._positions[order.ticker] = PaperPosition(
                account=self.account,
                ticker=order.ticker,
                quantity=new_qty,
                avg_cost=price,
                market_price=price,
                market_value=new_qty * price,
            )
        execution = PaperExecution(
            execution_id=f"exec_{len(self.executions) + 1:06d}",
            order_id=order.order_id,
            account=self.account,
            ticker=order.ticker,
            side=order.side,
            quantity=order.quantity,
            price=price,
            commission=0.0,
            executed_at=utc_now(),
        )
        self.executions.append(execution)
        return execution

    def positions(self) -> list[PaperPosition]:
        return list(self._positions.values())


def status_after_submit(order: PaperOrder) -> PaperOrder:
    """Mark an order FILLED after a MockBroker execution."""
    return order.model_copy(
        update={"status": OrderStatus.FILLED, "updated_at": utc_now()}
    )
