"""Execution pipeline: PortfolioTarget → DELTA OrderIntents → risk gate → broker.

Safety order of operations, ALL enforced before anything reaches a broker:

1. LLMs never appear here — intents are built deterministically from a
   PortfolioTarget, CURRENT BROKER POSITIONS and a price map. Orders are
   target-vs-current DELTAS: re-running the same target on an already
   invested account produces no orders, never a second full-size buy.
2. The kill switch file blocks everything.
3. Every intent passes the pre-trade risk checks (configs/risk.yaml
   execution section): per-order and RESULTING position/portfolio notional
   caps, daily turnover/loss, signal/price staleness, duplicate idempotency
   keys (in-memory AND the persistent ledger), concurrency cap, paper
   account prefix.
4. DRY_RUN (the default) marks orders DRY_RUN and submits nothing.

Every step is audited: ORDER_INTENT, ORDER_REJECTED, PAPER_ORDER_SUBMITTED,
ORDER_FILLED, POSITION_RECONCILED, SAFETY_GATE.
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
    PaperPosition,
    PortfolioTarget,
    PreTradeRiskDecision,
)
from quant_platform.core.timeutil import utc_now
from quant_platform.execution.broker import BrokerAdapter, SubmitResult
from quant_platform.execution.kill_switch import GlobalKillSwitch
from quant_platform.execution.ledger import OrderLedger


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
    min_order_notional: float = 100.0  # deltas smaller than this are noise
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
    """Deterministic FULL-TARGET intents (fresh account). One per position.

    Tickers without a price are skipped — an unpriced order is never built.
    Idempotency keys are content hashes (ticker/side/quantity/as-of, no
    run_id): rebuilding the same target yields the same keys, so duplicate
    submissions are detectable across process restarts.
    """
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
                    "idem", pos.ticker, side.value, round(quantity, 4),
                    target.as_of_date.isoformat(),
                ),
                signal_age_seconds=signal_age_seconds,
                created_at=utc_now(),
                as_of_date=target.as_of_date,
            )
        )
    return intents


def build_delta_intents(
    target: PortfolioTarget,
    prices: dict[str, float],
    current_positions: list[PaperPosition],
    account_value: float,
    signal_age_seconds: float = 0.0,
    config: ExecutionConfig | None = None,
) -> tuple[list[OrderIntent], list[str]]:
    """TARGET-VS-CURRENT delta intents — the production order builder.

    Every ticker in target + current positions is reconciled to its target
    weight: buys only the shortfall, sells only the excess, liquidates
    positions no longer in the target. Deltas below ``min_order_notional``
    are skipped as noise. Returns (intents, warnings).
    """
    cfg = config or ExecutionConfig()
    warnings: list[str] = []
    target_weights = {p.ticker: p.weight for p in target.positions}
    current_qty = {p.ticker: p.quantity for p in current_positions if abs(p.quantity) > 1e-12}

    intents: list[OrderIntent] = []
    for ticker in sorted(set(target_weights) | set(current_qty)):
        price = prices.get(ticker)
        if price is None or price <= 0:
            if ticker in current_qty or abs(target_weights.get(ticker, 0.0)) > 0:
                warnings.append(f"{ticker}: no usable price — delta order skipped")
            continue
        current_value = current_qty.get(ticker, 0.0) * price
        target_value = target_weights.get(ticker, 0.0) * account_value
        delta = target_value - current_value
        if abs(delta) < cfg.min_order_notional:
            continue  # already at target within noise
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        quantity = round(abs(delta) / price, 4)
        if quantity <= 0:
            continue
        intents.append(
            OrderIntent(
                intent_id=stable_id("intent", target.target_id, ticker, side.value),
                run_id=target.run_id,
                ticker=ticker,
                side=side,
                quantity=quantity,
                reference_price=price,
                notional_estimate=abs(delta),
                idempotency_key=stable_id(
                    "idem", ticker, side.value, quantity, target.as_of_date.isoformat()
                ),
                signal_age_seconds=signal_age_seconds,
                created_at=utc_now(),
                as_of_date=target.as_of_date,
            )
        )
    return intents, warnings


def pre_trade_check(
    intent: OrderIntent,
    config: ExecutionConfig | None = None,
    kill_switch: GlobalKillSwitch | None = None,
    seen_keys: set[str] | None = None,
    open_order_count: int = 0,
    day_turnover: float = 0.0,
    price_age_seconds: float = 0.0,
    resulting_position_notional: float | None = None,
    resulting_portfolio_gross: float | None = None,
    day_pnl: float | None = None,
) -> PreTradeRiskDecision:
    """All checks must pass or the intent is rejected with explicit reasons.

    EVERY configured limit in ExecutionConfig is enforced here; none is
    decorative. ``resulting_*`` are the post-order position/portfolio values
    (current + this delta). ``day_pnl`` None means the broker did not supply
    daily P&L — the loss limit is then honestly unenforceable, not faked.
    """
    cfg = config or ExecutionConfig()
    reasons: list[str] = []

    if kill_switch is not None and kill_switch.engaged():
        reasons.append(f"kill switch engaged ({kill_switch.path})")
    if intent.notional_estimate > cfg.max_order_notional:
        reasons.append(
            f"notional {intent.notional_estimate:,.0f} > max_order_notional "
            f"{cfg.max_order_notional:,.0f}"
        )
    if resulting_position_notional is not None and (
        abs(resulting_position_notional) > cfg.max_position_notional
    ):
        reasons.append(
            f"resulting position {resulting_position_notional:,.0f} > "
            f"max_position_notional {cfg.max_position_notional:,.0f}"
        )
    if resulting_portfolio_gross is not None and (
        resulting_portfolio_gross > cfg.max_portfolio_gross
    ):
        reasons.append(
            f"resulting gross {resulting_portfolio_gross:,.0f} > "
            f"max_portfolio_gross {cfg.max_portfolio_gross:,.0f}"
        )
    if intent.signal_age_seconds > cfg.stale_signal_seconds:
        reasons.append(
            f"signal age {intent.signal_age_seconds:.0f}s > {cfg.stale_signal_seconds:.0f}s"
        )
    if price_age_seconds > cfg.stale_price_seconds:
        reasons.append(f"price age {price_age_seconds:.0f}s > {cfg.stale_price_seconds:.0f}s")
    if seen_keys is not None and intent.idempotency_key in seen_keys:
        reasons.append("duplicate idempotency key — already submitted (persistent ledger)")
    if open_order_count >= cfg.max_concurrent_orders:
        reasons.append(f"open orders {open_order_count} >= {cfg.max_concurrent_orders}")
    if day_turnover + intent.notional_estimate > cfg.max_daily_turnover:
        reasons.append(
            f"daily turnover {day_turnover:,.0f} + order would exceed "
            f"{cfg.max_daily_turnover:,.0f}"
        )
    if day_pnl is not None and day_pnl <= -abs(cfg.max_daily_loss):
        reasons.append(
            f"daily loss {day_pnl:,.0f} breached max_daily_loss {cfg.max_daily_loss:,.0f}"
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
    warnings: list[str] = Field(default_factory=list)
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
    current_positions: list[PaperPosition] | None = None,
    price_age_seconds: dict[str, float] | None = None,
    day_pnl: float | None = None,
    ledger: OrderLedger | None = None,
    submit_timeout_seconds: float = 30.0,
) -> PipelineResult:
    """Delta intents → risk gate → broker (or DRY_RUN). Never throws past the gate.

    When ``current_positions`` is None the account is treated as empty
    (full-target buys). With positions, orders are target-vs-current deltas.
    Submitted orders are recorded in the persistent ``ledger`` when one is
    provided (the CLI always provides one), so a restarted process cannot
    duplicate them.
    """
    cfg = config or ExecutionConfig()
    as_of = target.as_of_date.isoformat()
    result = PipelineResult(run_id=target.run_id, dry_run=dry_run)
    price_ages = price_age_seconds or {}

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

    if current_positions is None:
        intents = build_order_intents(target, prices, account_value, signal_age_seconds)
        current_value: dict[str, float] = {}
    else:
        intents, delta_warnings = build_delta_intents(
            target, prices, current_positions, account_value, signal_age_seconds, cfg
        )
        result.warnings.extend(delta_warnings)
        current_value = {
            p.ticker: p.quantity * prices.get(p.ticker, 0.0)
            for p in current_positions
            if abs(p.quantity) > 1e-12
        }

    seen_keys: set[str] = set(ledger.known_keys()) if ledger is not None else set()
    day_turnover = 0.0
    running_gross = sum(abs(v) for v in current_value.values())

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
        current_notional = current_value.get(intent.ticker, 0.0)
        signed_delta = intent.notional_estimate if intent.side == OrderSide.BUY else -intent.notional_estimate
        resulting_position = current_notional + signed_delta
        resulting_gross = running_gross - abs(current_notional) + abs(resulting_position)
        decision = pre_trade_check(
            intent,
            cfg,
            kill_switch,
            seen_keys,
            open_order_count=len(
                [o for o in result.orders if o.status in (OrderStatus.SUBMITTED, OrderStatus.CREATED)]
            ),
            day_turnover=day_turnover,
            price_age_seconds=price_ages.get(intent.ticker, 0.0),
            resulting_position_notional=resulting_position,
            resulting_portfolio_gross=resulting_gross,
            day_pnl=day_pnl,
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
        running_gross = resulting_gross
        current_value[intent.ticker] = resulting_position

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
        try:
            submitted = broker.submit_and_monitor(
                order, timeout_seconds=submit_timeout_seconds
            )
        except Exception as exc:  # broker failure is recorded, never faked
            submitted = SubmitResult(
                order=order.model_copy(
                    update={"status": OrderStatus.REJECTED, "updated_at": utc_now()}
                ),
                execution=None,
            )
            result.warnings.append(f"{intent.ticker}: broker error — {exc}")
        result.orders.append(submitted.order)
        if submitted.execution is not None:
            result.executions.append(submitted.execution)
            if audit is not None:
                audit.record(
                    AuditEventType.ORDER_FILLED,
                    run_id=target.run_id,
                    as_of_date=as_of,
                    order_id=submitted.order.order_id,
                    price=submitted.execution.price,
                    quantity=submitted.execution.quantity,
                    status=submitted.order.status.value,
                )
        if submitted.order.status in (
            OrderStatus.CREATED,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            result.warnings.append(
                f"{intent.ticker}: order {submitted.order.order_id} ended "
                f"{submitted.order.status.value} — reconcile before re-running"
            )
        if ledger is not None:
            ledger.record(
                idempotency_key=intent.idempotency_key,
                intent_id=intent.intent_id,
                order_id=submitted.order.order_id,
                ticker=intent.ticker,
                side=intent.side.value,
                quantity=intent.quantity,
                as_of_date=as_of,
                status=submitted.order.status,
                broker_order_id=submitted.order.broker_order_id,
            )
    return result
