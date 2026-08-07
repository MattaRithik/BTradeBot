"""Paper-trading execution schemas (PAPER ONLY — no live trading exists here)."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from quant_platform.core.enums import OrderSide, OrderStatus, PlatformModel
from quant_platform.core.timeutil import UtcDatetime


class OrderIntent(PlatformModel):
    """Deterministic order request created by Python from a PortfolioTarget.
    LLMs never create these; only the execution pipeline does."""

    intent_id: str
    run_id: str
    ticker: str
    side: OrderSide
    quantity: float = Field(gt=0)
    order_type: str = "MKT"  # MKT | LMT
    limit_price: float | None = None
    reference_price: float  # last known price at intent creation
    notional_estimate: float = Field(ge=0)
    idempotency_key: str  # stable hash — duplicate intents share the key
    signal_age_seconds: float = Field(ge=0, default=0.0)
    created_at: UtcDatetime
    as_of_date: date


class PreTradeRiskDecision(PlatformModel):
    intent_id: str
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    checked_at: UtcDatetime


class PaperOrder(PlatformModel):
    order_id: str
    intent_id: str
    broker_order_id: str | None = None
    account: str = ""
    ticker: str
    side: OrderSide
    quantity: float = Field(gt=0)
    order_type: str = "MKT"
    limit_price: float | None = None
    status: OrderStatus = OrderStatus.CREATED
    dry_run: bool = True
    submitted_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class PaperExecution(PlatformModel):
    execution_id: str
    order_id: str
    account: str = ""
    ticker: str
    side: OrderSide
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    commission: float = Field(ge=0, default=0.0)
    executed_at: UtcDatetime


class PaperPosition(PlatformModel):
    account: str = ""
    ticker: str
    quantity: float
    avg_cost: float = 0.0
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None


class PaperAccountSnapshot(PlatformModel):
    account: str
    is_paper: bool = True  # must remain True; execution layer validates
    net_liquidation: float = 0.0
    cash: float = 0.0
    gross_exposure: float = 0.0
    positions: list[PaperPosition] = Field(default_factory=list)
    open_orders: int = 0
    captured_at: UtcDatetime
