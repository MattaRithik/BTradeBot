"""`quantctl backtest ...` — TRUE multi-period walk-forward backtest.

Every rebalance date re-runs the REAL research pipeline on data visible at
that date only, freezes a snapshot, and stitches the out-of-sample segments
into one equity curve. Runs are checkpointed per split and resumable.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quant_platform.backtest.walkforward import WalkForwardError, run_walkforward
from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import (
    EnvSettings,
    load_dotenv_if_present,
    load_yaml_config,
)
from quant_platform.core.enums import AuditEventType
from quant_platform.core.schemas import PredictionSnapshot, WalkForwardResult
from quant_platform.core.store import ArtifactStore
from quant_platform.research_runtime import build_market_source, run_research

backtest_app = typer.Typer(
    help="True walk-forward backtesting (checkpointed, resumable).",
    no_args_is_help=True,
)
console = Console()


def _snapshot_loader(store: ArtifactStore):
    def load(snapshot_id: str) -> PredictionSnapshot | None:
        path = store.dir("snapshots") / f"{snapshot_id}.json"
        if not path.exists():
            return None
        return PredictionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    return load


async def _run(
    *,
    start: date,
    end: date,
    rebalance: str,
    strategy: str,
    backtest_id: str | None,
) -> WalkForwardResult:
    settings = EnvSettings.from_env()
    if not settings.kimi_configured:
        raise WalkForwardError(
            "KIMI_API_KEY required — walk-forward reruns the real research pipeline "
            "at every rebalance date"
        )
    store = ArtifactStore(settings.data_root)
    audit = AuditLogger(settings.data_root / "logs" / "audit.jsonl")

    universe = list(load_yaml_config("universe")["college_test_universe"])
    from quant_platform.backtest.engine import load_backtest_config

    benchmarks = list(load_backtest_config().benchmarks)
    tickers = sorted(set(universe) | set(benchmarks))

    source, source_name = build_market_source(settings, settings.data_root)
    bars = source.get_history(tickers, start, end)
    if not bars:
        raise WalkForwardError(
            f"no market data via {source_name} for {len(tickers)} tickers over "
            f"{start}..{end} — on the Bloomberg machine run `quantctl bloomberg sync` first"
        )
    prices = pd.DataFrame([b.model_dump(mode="json") for b in bars])

    async def research_fn(as_of: date) -> PredictionSnapshot:
        summary = await run_research(
            settings.data_root,
            settings,
            as_of=as_of,
            audit=audit,
            with_backtest=False,
        )
        snapshot = _snapshot_loader(store)(summary["snapshot_id"])
        if snapshot is None:
            raise WalkForwardError(
                f"research run for {as_of} did not persist snapshot {summary['snapshot_id']}"
            )
        return snapshot

    audit.record(
        AuditEventType.BACKTEST_STARTED,
        run_id=backtest_id or "pending",
        start=start.isoformat(),
        end=end.isoformat(),
        rebalance=rebalance,
        strategy=strategy,
    )
    result = await run_walkforward(
        prices,
        research_fn,
        start=start,
        end=end,
        rebalance=rebalance,
        strategy=strategy,
        backtests_dir=settings.data_root / "backtests",
        backtest_id=backtest_id,
        audit=audit,
        snapshot_loader=_snapshot_loader(store),
    )
    audit.record(
        AuditEventType.BACKTEST_COMPLETED,
        run_id=result.backtest_id,
        splits=len(result.splits),
        cumulative_return=round(result.metrics.cumulative_return, 6) if result.metrics else None,
    )
    store.save_manifest(
        result.backtest_id,
        {
            "run_id": result.backtest_id,
            "kind": "walkforward_backtest",
            "start": result.start.isoformat(),
            "end": result.end.isoformat(),
            "rebalance": rebalance,
            "strategy": strategy,
            "splits": len(result.splits),
            "equity_curve_path": result.equity_curve_path,
            "warnings": result.warnings,
        },
    )
    return result


def _print_result(result: WalkForwardResult) -> None:
    console.print(f"[bold]BACKTEST:[/bold] {result.backtest_id}")
    console.print(
        f"[bold]WINDOW:[/bold] {result.start} .. {result.end}   "
        f"[bold]REBALANCE:[/bold] {result.rebalance}   [bold]STRATEGY:[/bold] {result.strategy}"
    )
    table = Table(title=f"WALK-FORWARD SPLITS ({len(result.splits)})")
    for col in ("as_of", "snapshot", "entry", "exit", "segment", "turnover", "cost", "pos"):
        table.add_column(col, justify="right" if col not in ("as_of", "snapshot") else "left")
    for s in result.splits:
        table.add_row(
            s.as_of_date.isoformat(),
            s.snapshot_id[:12],
            s.entry_date,
            s.exit_date,
            f"{s.segment_return * 100:+.2f}%",
            f"{s.turnover * 100:.1f}%",
            f"{s.cost * 100:.3f}%",
            str(s.n_positions),
        )
    console.print(table)
    if result.metrics:
        m = result.metrics
        console.print(
            f"cumulative={m.cumulative_return * 100:+.2f}%  "
            f"annualized={m.annualized_return * 100:+.2f}%  vol={m.annualized_volatility * 100:.2f}%  "
            f"sharpe={m.sharpe:.2f}  sortino={m.sortino:.2f}  maxDD={m.max_drawdown * 100:.2f}%\n"
            f"turnover={m.turnover * 100:.1f}%  cost drag={m.transaction_costs * 100:.3f}%  "
            + "  ".join(f"{k} {v * 100:+.2f}%" for k, v in result.benchmark_returns.items())
        )
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    console.print(f"equity curve: {result.equity_curve_path}")
    console.print(f"resume any time: quantctl backtest resume {result.backtest_id}")


@backtest_app.command("walk-forward")
def walk_forward(
    start: str = typer.Option(..., "--start", help="First decision window date YYYY-MM-DD."),
    end: str = typer.Option("latest", "--end", help="YYYY-MM-DD or 'latest' (latest available bar)."),
    rebalance: str = typer.Option("monthly", "--rebalance", help="monthly | weekly."),
    strategy: str = typer.Option("ensemble", "--strategy", help="Deterministic strategy builder name."),
) -> None:
    """Run a TRUE walk-forward backtest: fresh PIT research at every rebalance date."""
    load_dotenv_if_present()
    try:
        start_date = date.fromisoformat(start)
    except ValueError:
        raise typer.BadParameter(f"--start must be YYYY-MM-DD, got {start!r}") from None
    end_date: date | None = None
    if end != "latest":
        try:
            end_date = date.fromisoformat(end)
        except ValueError:
            raise typer.BadParameter(f"--end must be YYYY-MM-DD or 'latest', got {end!r}") from None
    if end_date is None:
        end_date = date.today()  # clamped to actual sessions inside the engine
    if rebalance not in ("monthly", "weekly"):
        raise typer.BadParameter("--rebalance must be 'monthly' or 'weekly'")

    try:
        result = asyncio.run(
            _run(start=start_date, end=end_date, rebalance=rebalance, strategy=strategy, backtest_id=None)
        )
    except WalkForwardError as exc:
        console.print(f"[red]walk-forward failed honestly:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    _print_result(result)


@backtest_app.command("resume")
def resume(
    backtest_id: str = typer.Argument(..., help="Backtest id printed by a previous walk-forward run."),
) -> None:
    """Resume an interrupted walk-forward backtest from its per-split checkpoint."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    state_path = settings.data_root / "backtests" / backtest_id / "state.json"
    if not state_path.exists():
        console.print(f"[red]no checkpoint found:[/red] {state_path}")
        raise typer.Exit(code=1)
    import json

    state = json.loads(state_path.read_text(encoding="utf-8"))
    try:
        result = asyncio.run(
            _run(
                start=date.fromisoformat(state["start"]),
                end=date.fromisoformat(state["end"]),
                rebalance=state.get("rebalance", "monthly"),
                strategy=state.get("strategy", "ensemble"),
                backtest_id=backtest_id,
            )
        )
    except WalkForwardError as exc:
        console.print(f"[red]walk-forward resume failed honestly:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    _print_result(result)
