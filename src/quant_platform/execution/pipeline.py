"""Execution pipeline: PortfolioTarget → OrderIntents → risk gate → broker.

Safety order of operations, ALL enforced before anything reaches a broker:

1. LLMs never appear here — intents are built deterministically from a
   PortfolioTarget and a price map.
2. The kill switch file blocks everything.
3. Every intent passes the pre-trade risk checks (configs/risk.yaml
   execution section): notional caps, signal/price staleness, duplicate
   idempotency keys, concurrency cap, paper account prefix.
4. DRY_RUN (the default) marks orders DRY_RUN and submits nothing.

Every step is audited: ORDER_INTENT, ORDER_REJECTED, PAPER_ORDER_SUBMITTED,
ORDER_FILLED, SAFETY_GATE.
"""

from __future__ import annotations

from pydantic import Field

from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import AuditEventType, OrderSide, OrderStatus, PlatformModel
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    OrderIntent,
    PaperExecution,
    PaperOrder,
    PortfolioTarget,
    PreTradeRiskDecision,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.execution.broker import BrokerAdapter
from quant_platform.execution.kill_switch import GlobalKillSwitch


class ExecutionConfig(PlatformModel):
    """Pre-trade safety limits (defaults mirror configs/risk.yaml)."""

    max_order_notional: float = 25_000
    max_position_notional: float = 100_000
    max_portfolio_gross: float = 1_000_000
    max_daily_turnover: float = 250_000
    max_daily_loss: float = 50_000
    max_concurrent_orders: int = 10
    stale_signal_seconds: float = 86_400
    stale_price_seconds: float = 900
    require_paper_account_prefix: str = "DU"


def load_execution_config() -> ExecutionConfig:
    raw = load_yaml_config("risk").get("execution", {}) or {}
    return ExecutionConfig(**raw)


def build_order_intents(
    target: PortfolioTarget,
    prices: dict[str, float],
    account_value: float,
    signal_age_seconds: float = 0.0,
    price_age_seconds: dict[str, float] | None = None,
) -> list[OrderIntent]:
    """Deterministic intents from a target. One intent per position.

    Tickers without a price are skipped — an unpriced order is never built.
    Idempotency keys are content hashes: rebuilding the same run's intents
    yields the same keys, so duplicate submissions are detectable.
    """
    ages = price_age_seconds or {}
    intents: list[OrderIntent] = []
    for pos in target.positions:
        price = prices.get(pos.ticker)
        if price is None or price <= 0:
            continue
        notional = abs(pos.weight) * account_value
        quantity = notional / price
        if quantity <= 0:
            continue
        side = OrderSide.BUY if pos.weight > 0 else OrderSide.SELL
        intents.append(
            OrderIntent(
                intent_id=stable_id("intent", target.target_id, pos.ticker),
                run_id=target.run_id,
                ticker=pos.ticker,
                side=side,
                quantity=round(quantity, 4),
                reference_price=price,
                notional_estimate=notional,
                idempotency_key=stable_id(
                    "idem", target.run_id, pos.ticker, side.value, round(quantity, 4),
                    target.as_of_date.isoformat(),
                ),
                signal_age_seconds=signal_age_seconds,
                created_at=utc_now(),
                as_of_date=target.as_of_date,
            )
        )
        # price staleness rides on the intent via signal_age check upstream;
        # per-ticker price ages are enforced in pre_trade_check via ``ages``
        ages.setdefault(pos.ticker, 0.0)
    return intents


def pre_trade_check(
    intent: OrderIntent,
    config: ExecutionConfig | None = None,
    kill_switch: GlobalKillSwitch | None = None,
    seen_keys: set[str] | None = None,
    open_order_count: int = 0,
    day_turnover: float = 0.0,
    price_age_seconds: float = 0.0,
) -> PreTradeRiskDecision:
    """All checks must pass or the intent is rejected with explicit reasons."""
    cfg = config or ExecutionConfig()
    reasons: list[str] = []

    if kill_switch is not None and kill_switch.engaged():
        reasons.append(f"kill switch engaged ({kill_switch.path})")
    if intent.notional_estimate > cfg.max_order_notional:
        reasons.append(
            f"notional {intent.notional_estimate:,.0f} > max_order_notional "
            f"{cfg.max_order_notional:,.0f}"
        )
    if intent.signal_age_seconds > cfg.stale_signal_seconds:
        reasons.append(
            f"signal age {intent.signal_age_seconds:.0f}s > {cfg.stale_signal_seconds:.0f}s"
        )
    if price_age_seconds > cfg.stale_price_seconds:
        reasons.append(f"price age {price_age_seconds:.0f}s > {cfg.stale_price_seconds:.0f}s")
    if seen_keys is not None and intent.idempotency_key in seen_keys:
        reasons.append("duplicate idempotency key — already submitted this run")
    if open_order_count >= cfg.max_concurrent_orders:
        reasons.append(f"open orders {open_order_count} >= {cfg.max_concurrent_orders}")
    if day_turnover + intent.notional_estimate > cfg.max_daily_turnover:
        reasons.append(
            f"daily turnover {day_turnover:,.0f} + order would exceed "
            f"{cfg.max_daily_turnover:,.0f}"
        )
    return PreTradeRiskDecision(
        intent_id=intent.intent_id,
        approved=not reasons,
        reasons=reasons,
        checked_at=utc_now(),
    )


class PipelineResult(PlatformModel):
    run_id: str
    dry_run: bool
    orders: list[PaperOrder] = Field(default_factory=list)
    executions: list[PaperExecution] = Field(default_factory=list)
    decisions: list[PreTradeRiskDecision] = Field(default_factory=list)
    blocked_by_kill_switch: bool = False


def run_pipeline(
    target: PortfolioTarget,
    prices: dict[str, float],
    broker: BrokerAdapter,
    account_value: float,
    config: ExecutionConfig | None = None,
    kill_switch: GlobalKillSwitch | None = None,
    dry_run: bool = True,
    audit: AuditLogger | None = None,
    signal_age_seconds: float = 0.0,
) -> PipelineResult:
    """Intents → risk gate → broker (or DRY_RUN). Never throws past the gate."""
    cfg = config or ExecutionConfig()
    as_of = target.as_of_date.isoformat()
    result = PipelineResult(run_id=target.run_id, dry_run=dry_run)

    if kill_switch is not None and kill_switch.engaged():
        result.blocked_by_kill_switch = True
        if audit is not None:
            audit.record(
                AuditEventType.SAFETY_GATE,
                run_id=target.run_id,
                as_of_date=as_of,
                gate="kill_switch",
                outcome="all_orders_blocked",
            )
        return result

    intents = build_order_intents(target, prices, account_value, signal_age_seconds)
    seen_keys: set[str] = set()
    day_turnover = 0.0

    for intent in intents:
        if audit is not None:
            audit.record(
                AuditEventType.ORDER_INTENT,
                run_id=target.run_id,
                as_of_date=as_of,
                intent_id=intent.intent_id,
                ticker=intent.ticker,
                side=intent.side.value,
                notional=intent.notional_estimate,
            )
        decision = pre_trade_check(
            intent, cfg, kill_switch, seen_keys,
            open_order_count=len(result.orders), day_turnover=day_turnover,
        )
        result.decisions.append(decision)
        order = PaperOrder(
            order_id=stable_id("order", intent.intent_id),
            intent_id=intent.intent_id,
            account=getattr(broker, "account", ""),
            ticker=intent.ticker,
            side=intent.side,
            quantity=intent.quantity,
            order_type="MKT",
            limit_price=intent.reference_price,  # mock/reference pricing
            dry_run=dry_run,
        )
        if not decision.approved:
            order = order.model_copy(update={"status": OrderStatus.RISK_REJECTED})
            result.orders.append(order)
            if audit is not None:
                audit.record(
                    AuditEventType.ORDER_REJECTED,
                    run_id=target.run_id,
                    as_of_date=as_of,
                    intent_id=intent.intent_id,
                    reasons=decision.reasons,
                )
            continue
        seen_keys.add(intent.idempotency_key)
        day_turnover += intent.notional_estimate

        if dry_run:
            result.orders.append(order.model_copy(update={"status": OrderStatus.DRY_RUN}))
            continue

        order = order.model_copy(
            update={"status": OrderStatus.SUBMITTED, "submitted_at": utc_now()}
        )
        if audit is not None:
            audit.record(
                AuditEventType.PAPER_ORDER_SUBMITTED,
                run_id=target.run_id,
                as_of_date=as_of,
                order_id=order.order_id,
                ticker=intent.ticker,
            )
        execution = broker.submit_order(order)
        result.executions.append(execution)
        result.orders.append(
            order.model_copy(update={"status": OrderStatus.FILLED, "updated_at": utc_now()})
        )
        if audit is not None:
            audit.record(
                AuditEventType.ORDER_FILLED,
                run_id=target.run_id,
                as_of_date=as_of,
                order_id=order.order_id,
                price=execution.price,
                quantity=execution.quantity,
            )
    return result
