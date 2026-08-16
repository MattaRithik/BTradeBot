"""`quantctl paper ...` — PAPER-trading diagnostics, preview, execution, reconcile.

PAPER ONLY. Real submission requires ALL of: trading_mode=paper, a DU*
account, a reachable paper TWS/Gateway session, DRY_RUN=false, and the
explicit --confirm-paper flag. Anything less submits nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.config import EnvSettings, load_dotenv_if_present
from quant_platform.core.schemas import PortfolioTarget, PredictionSnapshot
from quant_platform.core.store import ArtifactStore
from quant_platform.execution import (
    ExecutionConfig,
    GlobalKillSwitch,
    MockBroker,
    OrderLedger,
    load_execution_config,
    reconcile_positions,
    run_pipeline,
    validate_paper_account,
)
from quant_platform.execution.broker import BrokerAdapter, BrokerError
from quant_platform.execution.ibkr_paper import IB_ASYNC_AVAILABLE

paper_app = typer.Typer(help="Paper trading (PAPER ONLY — no live trading).", no_args_is_help=True)
console = Console()


def _load_snapshot(store: ArtifactStore, snapshot_id: str) -> PredictionSnapshot:
    if snapshot_id == "latest":
        path = store.latest("snapshots")
        if path is None:
            console.print("[red]no snapshots found[/red] — run `quantctl research run` first")
            raise typer.Exit(code=1)
        snapshot_id = path.stem
    path = store.dir("snapshots") / f"{snapshot_id}.json"
    if not path.exists():
        console.print(f"[red]snapshot not found:[/red] {path}")
        raise typer.Exit(code=1)
    return PredictionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def _target_from_snapshot(snapshot: PredictionSnapshot) -> PortfolioTarget:
    if snapshot.portfolio is None or not snapshot.portfolio.positions:
        console.print(
            f"[yellow]snapshot {snapshot.snapshot_id} froze an all-cash portfolio — "
            "no orders to compute (cash is a valid decision)[/yellow]"
        )
        raise typer.Exit(code=0)
    return snapshot.portfolio


def _latest_prices(settings: EnvSettings, tickers: list[str]) -> dict[str, float]:
    """Latest available close per ticker via the production market source."""
    from quant_platform.research_runtime import build_market_source

    source, source_name = build_market_source(settings, settings.data_root)
    bars = source.get_history(tickers, date.today() - timedelta(days=10), date.today())
    prices: dict[str, float] = {}
    for bar in bars:  # keep the newest observation per ticker
        prices[bar.ticker] = bar.close
    if not prices:
        console.print(
            f"[red]no recent prices via {source_name}[/red] — "
            "on the Bloomberg machine run `quantctl bloomberg sync` first"
        )
        raise typer.Exit(code=1)
    return prices


def _broker(settings: EnvSettings, *, require_real: bool) -> BrokerAdapter:
    """Real IBKR paper adapter when available; MockBroker only for previews."""
    if IB_ASYNC_AVAILABLE:
        from quant_platform.execution.ibkr_paper import IBKRPaperBroker

        try:
            broker: BrokerAdapter = IBKRPaperBroker(settings)
            broker.connect()
            return broker
        except BrokerError as exc:
            if require_real:
                console.print(f"[red]IBKR paper session unavailable:[/red] {exc}")
                raise typer.Exit(code=1) from exc
            console.print(f"[yellow]IBKR unavailable ({exc}) — preview against MockBroker[/yellow]")
    elif require_real:
        console.print(
            "[red]ib_async not installed[/red] — real paper execution needs the 'ibkr' extra "
            "and a logged-in PAPER TWS/Gateway session"
        )
        raise typer.Exit(code=1)
    broker = MockBroker(account=settings.ibkr_account or "DU1234567")
    broker.connect()
    return broker


@paper_app.command("doctor")
def doctor() -> None:
    """Probe the paper-trading stack honestly: env, account prefix, kill switch, adapters."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    cfg: ExecutionConfig = load_execution_config()
    table = Table(title="quantctl paper doctor — paper-trading checks")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    failures = 0

    def row(name: str, ok: bool, detail: str, warn: bool = False) -> None:
        nonlocal failures
        status = "PASS" if ok else ("WARN" if warn else "FAIL")
        if status == "FAIL":
            failures += 1
        style = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[status]
        table.add_row(name, f"[{style}]{status}[/{style}]", detail)

    row("trading mode", settings.trading_mode == "paper",
        f"trading_mode={settings.trading_mode}")
    row("dry run", True,
        f"dry_run={settings.dry_run}" + (" — nothing will be submitted" if settings.dry_run
                                         else " — paper orders MAY be submitted"),
        warn=not settings.dry_run)
    try:
        account = validate_paper_account(settings.ibkr_account,
                                         cfg.require_paper_account_prefix)
        row("paper account", True, f"{account} (prefix {cfg.require_paper_account_prefix}*)")
    except BrokerError as exc:
        row("paper account", False, str(exc), warn=not settings.ibkr_account)
    row("live port guard", settings.ibkr_port not in (7496, 4001),
        f"IBKR_PORT={settings.ibkr_port} (paper ports: 7497 TWS / 4002 Gateway)")
    ks = GlobalKillSwitch()
    row("kill switch", True, f"{'ENGAGED — all orders blocked' if ks.engaged() else 'clear'} "
                             f"({ks.path})", warn=ks.engaged())
    row("ibkr client", IB_ASYNC_AVAILABLE,
        "ib_async installed" if IB_ASYNC_AVAILABLE
        else "ib_async not installed — MockBroker path only (expected off-gateway)",
        warn=not IB_ASYNC_AVAILABLE)
    ledger = OrderLedger()
    row("order ledger", True, f"{ledger.path} ({len(ledger.known_keys())} recorded intents)")
    row("mock broker", True, "MockBroker always available for offline runs")

    console.print(table)
    console.print("[bold]PAPER TRADING ONLY — live accounts are refused by design[/bold]")
    if failures:
        raise typer.Exit(code=1)


@paper_app.command("preview")
def preview(
    snapshot_id: str = typer.Option("latest", "--snapshot", help="Snapshot id or 'latest'."),
) -> None:
    """Compute target-vs-current DELTA orders for a frozen snapshot. Submits nothing."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    store = ArtifactStore(settings.data_root)
    snapshot = _load_snapshot(store, snapshot_id)
    target = _target_from_snapshot(snapshot)

    broker = _broker(settings, require_real=False)
    account = broker.account_snapshot()
    tickers = sorted({p.ticker for p in target.positions}
                     | {p.ticker for p in account.positions})
    prices = _latest_prices(settings, tickers)

    result = run_pipeline(
        target,
        prices,
        broker,
        account_value=account.net_liquidation or 1.0,
        config=load_execution_config(),
        kill_switch=GlobalKillSwitch(),
        dry_run=True,
        current_positions=account.positions,
        day_pnl=account.day_pnl,
    )
    _print_orders(snapshot, result.orders, result.decisions, result.warnings)
    console.print("[green]preview only[/green] — nothing was submitted")


@paper_app.command("execute")
def execute(
    snapshot_id: str = typer.Option("latest", "--snapshot", help="Snapshot id or 'latest'."),
    confirm_paper: bool = typer.Option(
        False, "--confirm-paper", help="Required explicit confirmation to submit PAPER orders."
    ),
) -> None:
    """Submit delta orders to the IBKR PAPER account. Requires --confirm-paper and DRY_RUN=false."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    store = ArtifactStore(settings.data_root)
    snapshot = _load_snapshot(store, snapshot_id)
    target = _target_from_snapshot(snapshot)

    if not confirm_paper or settings.dry_run:
        broker = _broker(settings, require_real=False)
        account = broker.account_snapshot()
        tickers = sorted({p.ticker for p in target.positions}
                         | {p.ticker for p in account.positions})
        prices = _latest_prices(settings, tickers)
        result = run_pipeline(
            target, prices, broker, account_value=account.net_liquidation or 1.0,
            config=load_execution_config(), kill_switch=GlobalKillSwitch(),
            dry_run=True, current_positions=account.positions, day_pnl=account.day_pnl,
        )
        _print_orders(snapshot, result.orders, result.decisions, result.warnings)
        why = []
        if not confirm_paper:
            why.append("--confirm-paper not given")
        if settings.dry_run:
            why.append("DRY_RUN=true")
        console.print(f"[yellow]NOTHING SUBMITTED[/yellow] ({'; '.join(why)})")
        return

    broker = _broker(settings, require_real=True)
    account = broker.account_snapshot()
    tickers = sorted({p.ticker for p in target.positions}
                     | {p.ticker for p in account.positions})
    prices = _latest_prices(settings, tickers)
    from quant_platform.core.audit import AuditLogger

    result = run_pipeline(
        target, prices, broker, account_value=account.net_liquidation or 1.0,
        config=load_execution_config(), kill_switch=GlobalKillSwitch(),
        dry_run=False, current_positions=account.positions, day_pnl=account.day_pnl,
        audit=AuditLogger(settings.data_root / "logs" / "audit.jsonl"),
        ledger=OrderLedger(),
    )
    _print_orders(snapshot, result.orders, result.decisions, result.warnings)
    console.print(
        f"[bold]submitted to PAPER account {account.account}[/bold] — "
        "run `quantctl paper reconcile` to verify positions vs target"
    )


@paper_app.command("reconcile")
def reconcile(
    snapshot_id: str = typer.Option("latest", "--snapshot", help="Snapshot id or 'latest'."),
) -> None:
    """Compare current PAPER broker positions against the frozen target. Read-only."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    store = ArtifactStore(settings.data_root)
    snapshot = _load_snapshot(store, snapshot_id)
    target = _target_from_snapshot(snapshot)

    broker = _broker(settings, require_real=True)
    account = broker.account_snapshot()
    tickers = sorted({p.ticker for p in target.positions}
                     | {p.ticker for p in account.positions})
    prices = _latest_prices(settings, tickers)
    report = reconcile_positions(target, account, prices)
    store.save_model("paper_reconciliations",
                     f"{report.target_id}_{report.checked_at[:19].replace(':', '-')}", report)

    console.print(
        f"[bold]ACCOUNT:[/bold] {report.account}  value={report.account_value:,.0f}  "
        f"cash={report.cash:,.0f} (target {report.cash_target:,.0f})"
    )
    if report.reconciled:
        console.print("[green]RECONCILED[/green] — positions match the frozen target")
    else:
        table = Table(title="DISCREPANCIES (target - current)")
        for col in ("ticker", "target", "current", "delta", "delta %"):
            table.add_column(col, justify="right")
        for d in report.discrepancies:
            table.add_row(d.ticker, f"{d.target_value:,.0f}", f"{d.current_value:,.0f}",
                          f"{d.delta_value:+,.0f}", f"{d.delta_pct_of_account * 100:+.2f}%")
        console.print(table)
        raise typer.Exit(code=1)


@paper_app.command("kill-switch")
def kill_switch_cmd(
    action: str = typer.Argument(..., help="status | engage | disengage"),
    reason: str = typer.Option("manual", "--reason"),
) -> None:
    """Engage/disengage the global kill switch (a file that blocks ALL new orders)."""
    load_dotenv_if_present()
    from quant_platform.core.audit import AuditLogger
    from quant_platform.core.config import EnvSettings as _Env

    ks = GlobalKillSwitch(audit=AuditLogger(_Env.from_env().data_root / "logs" / "audit.jsonl"))
    if action == "status":
        console.print(f"{'ENGAGED' if ks.engaged() else 'clear'} ({ks.path})")
    elif action == "engage":
        ks.engage(reason)
        console.print(f"[red]KILL SWITCH ENGAGED[/red] — all new paper orders blocked ({ks.path})")
    elif action == "disengage":
        ks.disengage(reason)
        console.print(f"[green]kill switch disengaged[/green] ({ks.path})")
    else:
        raise typer.BadParameter("action must be status | engage | disengage")


@paper_app.command("dry-run")
def dry_run() -> None:
    """Build a tiny sample target and run the full pipeline against MockBroker.

    Proves the safety gate end-to-end without submitting anything, regardless
    of the DRY_RUN env flag.
    """
    from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget

    load_dotenv_if_present()
    target = PortfolioTarget(
        target_id="tgt_demo",
        run_id="paper_dry_run",
        strategy="long_basket",
        as_of_date=date.today(),
        positions=[PortfolioPosition(ticker="NVDA", weight=0.02)],
        cash_weight=0.98,
        gross_exposure=0.02,
        net_exposure=0.02,
    )
    broker = MockBroker()
    broker.connect()
    result = run_pipeline(
        target, {"NVDA": 100.0}, broker, account_value=broker.cash, dry_run=True
    )
    for order in result.orders:
        console.print(f"  {order.ticker} {order.side.value} {order.quantity} -> {order.status.value}")
    console.print(
        f"[green]dry-run complete[/green]: {len(result.orders)} order(s) computed, "
        "0 submitted (dry-run is unconditional in this command)"
    )


def _print_orders(
    snapshot: PredictionSnapshot, orders, decisions, warnings: list[str]
) -> None:
    console.print(f"[bold]SNAPSHOT:[/bold] {snapshot.snapshot_id} (as_of {snapshot.as_of_date})")
    table = Table(title="DELTA ORDERS (target vs current paper positions)")
    for col in ("ticker", "side", "qty", "ref px", "notional", "status"):
        table.add_column(col, justify="right" if col in ("qty", "ref px", "notional") else "left")
    decisions_by_intent = {d.intent_id: d for d in decisions}
    for order in orders:
        decision = decisions_by_intent.get(order.intent_id)
        status = order.status.value
        if decision is not None and not decision.approved:
            status = f"{status}: {'; '.join(decision.reasons)}"
        table.add_row(
            order.ticker, order.side.value, f"{order.quantity:,.2f}",
            f"{order.limit_price or 0:,.2f}",
            f"{order.quantity * (order.limit_price or 0):,.0f}", status,
        )
    console.print(table)
    if not orders:
        console.print("no orders — account already matches the frozen target (within noise)")
    for warning in warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
