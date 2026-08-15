"""`quantctl research ...` — REAL research runtime (Bloomberg + Kimi).

The offline demo (`quantctl demo`) needs nothing external; this app is the
real path and fails loudly when Bloomberg data, exported news, or the Kimi
gateway is unavailable. Safety gate first: paper + dry-run only, always.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.config import EnvSettings, load_dotenv_if_present, load_yaml_config
from quant_platform.data.bloomberg_desktop import BloombergDesktopAdapter
from quant_platform.data.bloomberg_export import BloombergExportAdapter, BloombergExportError
from quant_platform.models import ModelProviderError
from quant_platform.research_runtime import (
    ResearchRuntimeError,
    kimi_doctor_ping,
    newscatcher_doctor_ping,
    run_research,
)

research_app = typer.Typer(help="Real research runtime (Bloomberg + Kimi).", no_args_is_help=True)
console = Console()

_STATUS_STYLE = {"PASS": "green", "FAIL": "red", "WARN": "yellow", "NOT_ENTITLED": "yellow", "SKIPPED": "dim"}


def _row(table: Table, check: str, status: str, detail: str) -> None:
    style = _STATUS_STYLE.get(status, "")
    table.add_row(check, f"[{style}]{status}[/{style}]", detail)


@research_app.command("doctor")
def doctor() -> None:
    """Readiness for a REAL research run. No strategy is executed."""
    from quant_platform.cli.bloomberg import _render

    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    inbox = Path(load_yaml_config("bloomberg")["export"]["inbox"])

    table = Table(title="quantctl research doctor — real-run readiness")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    failures = 0

    # 1. safety gate — the runtime refuses anything but paper + dry-run
    if settings.trading_mode == "paper" and settings.dry_run:
        _row(table, "safety gate", "PASS", "trading_mode=paper dry_run=true")
    else:
        _row(
            table,
            "safety gate",
            "FAIL",
            f"trading_mode={settings.trading_mode} dry_run={settings.dry_run} — refused",
        )
        failures += 1

    # 2./3. Bloomberg sources (either one is sufficient for a real run)
    desktop_diag = BloombergDesktopAdapter(
        host=settings.bloomberg_host, port=settings.bloomberg_port
    ).diagnose()
    _render(desktop_diag)
    _row(
        table,
        "bloomberg desktop",
        "PASS" if desktop_diag.available else "WARN",
        "desktop API usable" if desktop_diag.available else "unavailable — export inbox is the fallback",
    )
    export_diag = BloombergExportAdapter(inbox).diagnose()
    _render(export_diag)
    _row(
        table,
        "bloomberg export",
        "PASS" if export_diag.available else "WARN",
        f"inbox {inbox} usable" if export_diag.available else f"no usable exports in {inbox}",
    )
    if not (desktop_diag.available or export_diag.available):
        _row(table, "market data source", "FAIL", "neither desktop API nor export inbox can serve bars")
        failures += 1
    else:
        source = "bloomberg_desktop" if desktop_diag.available else "bloomberg_export"
        _row(table, "market data source", "PASS", f"will use {source}")

    # 4. news sources: NewsCatcher API (primary) + exported Bloomberg news (fallback)
    news_dir = inbox / "news"
    news_files = (
        sorted(p for p in news_dir.iterdir() if p.suffix.lower() in {".csv", ".xlsx", ".xls"})
        if news_dir.is_dir()
        else []
    )
    if news_files:
        _row(table, "exported news", "PASS", f"{len(news_files)} file(s) in {news_dir}")
    else:
        _row(table, "exported news", "WARN", f"no news CSV/XLSX in {news_dir}")

    newscatcher_ok = False
    if not settings.newscatcher_configured:
        _row(
            table,
            "newscatcher api",
            "WARN",
            "NEWSCATCHER_API_KEY not set — Bloomberg export news is the fallback",
        )
    else:
        status, detail = asyncio.run(newscatcher_doctor_ping(settings))
        _row(table, "newscatcher api", status, detail)
        if status == "FAIL":
            failures += 1  # the user configured it; it must work
        else:
            newscatcher_ok = True

    if newscatcher_ok and news_files:
        _row(table, "news source", "PASS", "NewsCatcher API (primary) + Bloomberg export news")
    elif newscatcher_ok:
        _row(table, "news source", "PASS", "NewsCatcher API (primary)")
    elif news_files:
        _row(table, "news source", "PASS", f"Bloomberg export news from {news_dir} (fallback)")
    else:
        _row(
            table,
            "news source",
            "FAIL",
            "no news source — set NEWSCATCHER_API_KEY or export news CSV/XLSX into the inbox",
        )
        failures += 1

    # 5. Kimi real ping (one minimal call)
    if not settings.kimi_configured:
        _row(table, "kimi gateway", "FAIL", "KIMI_API_KEY not set")
        failures += 1
    else:
        status, detail = asyncio.run(kimi_doctor_ping(settings))
        _row(table, "kimi gateway", status, detail)
        if status == "FAIL":
            failures += 1

    console.print(table)
    if failures:
        console.print(f"[red]{failures} check(s) FAILED — fix them before `quantctl research run`[/red]")
        raise typer.Exit(code=1)
    console.print("[green]ready for a real research run[/green]")


@research_app.command("run")
def run(
    as_of: str | None = typer.Option(
        None, "--as-of", help="Research cutoff YYYY-MM-DD (default: ~63 trading days before the last bar)."
    ),
    days: int = typer.Option(400, "--days", help="History length ending today."),
    export_only: bool = typer.Option(
        False, "--export-only", help="Use the export inbox even if blpapi is present."
    ),
    no_backtest: bool = typer.Option(
        False, "--no-backtest", help="Freeze the snapshot without opening the test window."
    ),
) -> None:
    """Run the REAL research pipeline: Bloomberg data, Kimi reasoning, frozen snapshot."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()

    if settings.trading_mode != "paper" or not settings.dry_run:
        console.print("[red]refused:[/red] TRADING_MODE must be 'paper' and DRY_RUN must be true")
        raise typer.Exit(code=1)
    if not settings.kimi_configured:
        console.print(
            "[red]KIMI_API_KEY required for research run[/red] — "
            "the offline demo (quantctl demo) covers no-key runs"
        )
        raise typer.Exit(code=1)

    parsed_as_of: date | None = None
    if as_of:
        try:
            parsed_as_of = date.fromisoformat(as_of)
        except ValueError:
            raise typer.BadParameter(f"--as-of must be YYYY-MM-DD, got {as_of!r}") from None

    adapter = None
    if export_only:
        inbox = Path(load_yaml_config("bloomberg")["export"]["inbox"])
        adapter = BloombergExportAdapter(inbox)

    try:
        summary = asyncio.run(
            run_research(
                settings.data_root,
                settings,
                as_of=parsed_as_of,
                history_days=days,
                market_adapter=adapter,
                with_backtest=not no_backtest,
            )
        )
    except (ResearchRuntimeError, ModelProviderError, BloombergExportError, ConnectionError) as exc:
        console.print(f"[red]research run failed honestly:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[bold green]research run complete[/bold green] run_id={summary['run_id']}")
    console.print(f"as_of={summary['as_of_date']}  test_window={summary['test_window']}")
    console.print(
        f"data_source={summary['data_source']}  provider={summary['provider']}  "
        f"news_dir={summary['news_dir']}"
    )
    console.print(
        f"bars={summary['bars_visible']}  news={summary['news_visible']}  "
        f"evidence={summary['evidence_cards']}  theses={summary['theses']}"
    )
    console.print(f"selected sectors: {summary['selected_sectors']}")
    console.print(
        f"portfolio: {summary['portfolio_positions']} position(s), cash_weight={summary['cash_weight']}"
    )
    console.print(f"snapshot: {summary['snapshot_id']}")
    if summary["backtest"]:
        bt = summary["backtest"]
        console.print(
            f"backtest: cumulative_return={bt['cumulative_return']:.4f}  "
            f"sharpe={bt['sharpe']:.2f}  max_drawdown={bt['max_drawdown']:.4f}  "
            f"benchmarks={summary['benchmarks']}"
        )
    else:
        console.print("[yellow]backtest skipped — snapshot frozen, evaluate later[/yellow]")
    if summary["failure_record"]:
        console.print(f"failure record: {summary['failure_record']}")
    if summary["model_cost_usd"] is not None:
        console.print(f"model: calls={summary['model_calls']}  cost_usd={summary['model_cost_usd']:.6f}")
    for warning in summary["warnings"]:
        console.print(f"[yellow]warning:[/yellow] {warning}")
