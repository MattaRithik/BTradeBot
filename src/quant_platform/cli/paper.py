"""`quantctl paper ...` — paper-trading diagnostics and dry runs."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.config import EnvSettings, load_dotenv_if_present
from quant_platform.execution import (
    ExecutionConfig,
    GlobalKillSwitch,
    MockBroker,
    load_execution_config,
    validate_paper_account,
)
from quant_platform.execution.broker import BrokerError
from quant_platform.execution.ibkr_paper import IB_ASYNC_AVAILABLE

paper_app = typer.Typer(help="Paper trading (PAPER ONLY — no live trading).", no_args_is_help=True)
console = Console()


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
    ks = GlobalKillSwitch()
    row("kill switch", True, f"{'ENGAGED — all orders blocked' if ks.engaged() else 'clear'} "
                             f"({ks.path})", warn=ks.engaged())
    row("ibkr client", IB_ASYNC_AVAILABLE,
        "ib_async installed" if IB_ASYNC_AVAILABLE
        else "ib_async not installed — MockBroker path only (expected off-gateway)",
        warn=not IB_ASYNC_AVAILABLE)
    row("mock broker", True, "MockBroker always available for offline runs")

    console.print(table)
    console.print("[bold]PAPER TRADING ONLY — live accounts are refused by design[/bold]")
    if failures:
        raise typer.Exit(code=1)


@paper_app.command("dry-run")
def dry_run() -> None:
    """Build a tiny sample target and run the full pipeline against MockBroker.

    Proves the safety gate end-to-end without submitting anything, regardless
    of the DRY_RUN env flag.
    """
    from datetime import date

    from quant_platform.core.schemas import PortfolioPosition, PortfolioTarget
    from quant_platform.execution import run_pipeline

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
