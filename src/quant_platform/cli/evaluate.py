"""`quantctl evaluate ...` — frozen-snapshot evaluation ("time machine" test).

Evaluates the EXACT frozen portfolio of a persisted snapshot at standard
horizons (1M/2M/3M/6M/1Y/latest available market date) against the
configured benchmarks. Research is NEVER re-run here; the snapshot must pass
integrity verification before any post-cutoff price data is touched.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.config import EnvSettings, load_dotenv_if_present
from quant_platform.core.schemas import PredictionSnapshot
from quant_platform.core.store import ArtifactStore
from quant_platform.evaluation import EvaluationError, evaluate_snapshot
from quant_platform.research_runtime import build_market_source

evaluate_app = typer.Typer(
    help="Frozen-snapshot evaluation (never reruns research).", no_args_is_help=True
)
console = Console()


def _load_snapshot(store: ArtifactStore, snapshot_id: str) -> PredictionSnapshot:
    if snapshot_id == "latest":
        path = store.latest("snapshots")
        if path is None:
            raise EvaluationError("no snapshots found — run `quantctl research run` first")
        snapshot_id = path.stem
    path = store.dir("snapshots") / f"{snapshot_id}.json"
    if not path.exists():
        raise EvaluationError(f"snapshot not found: {path}")
    return PredictionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


@evaluate_app.command("snapshot")
def evaluate_snapshot_cmd(
    snapshot_id: str = typer.Argument(..., help="Snapshot id, or 'latest'."),
    through: str = typer.Option(
        "latest", "--through", help="Evaluate through YYYY-MM-DD or 'latest' (latest available bar)."
    ),
) -> None:
    """Evaluate a frozen snapshot forward: horizons, benchmarks, costs, contributors."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    store = ArtifactStore(settings.data_root)

    try:
        snapshot = _load_snapshot(store, snapshot_id)
        through_date = None if through == "latest" else date.fromisoformat(through)

        tickers = sorted(
            {p.ticker for p in (snapshot.portfolio.positions if snapshot.portfolio else [])}
            | set(load_backtest_benchmarks())
        )
        source, source_name = build_market_source(settings, settings.data_root)
        bars = source.get_history(tickers, snapshot.as_of_date, through_date or date.today())
        if not bars:
            raise EvaluationError(
                f"no post-cutoff market data via {source_name} for {tickers} — "
                "on the Bloomberg machine run `quantctl bloomberg sync` first"
            )
        prices = pd.DataFrame([b.model_dump(mode="json") for b in bars])
        result = evaluate_snapshot(snapshot, prices, through=through_date)
    except (EvaluationError, ValueError) as exc:
        console.print(f"[red]evaluation failed honestly:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    store.save_model("evaluations", result.evaluation_id, result)

    console.print(f"[bold]SNAPSHOT:[/bold] {result.snapshot_id}")
    console.print(
        f"[bold]AS OF:[/bold] {result.visible_cutoff} ({result.cutoff_timezone or 'UTC'})"
        f"   [bold]ENTRY:[/bold] {result.entry_date} ({result.execution_convention})"
    )
    if snapshot.portfolio:
        holdings = Table(title="FROZEN PORTFOLIO")
        holdings.add_column("ticker")
        holdings.add_column("weight", justify="right")
        for p in sorted(snapshot.portfolio.positions, key=lambda p: -p.weight):
            holdings.add_row(p.ticker, f"{p.weight * 100:.1f}%")
        holdings.add_row("CASH", f"{snapshot.portfolio.cash_weight * 100:.1f}%")
        console.print(holdings)

    table = Table(title="PERFORMANCE (frozen buy-and-hold, costs included)")
    table.add_column("")
    for h in result.horizons:
        table.add_column(h.horizon, justify="right")
    table.add_row("BTradeBot", *[f"{h.portfolio_return * 100:+.2f}%" for h in result.horizons])
    benchmarks = sorted({b for h in result.horizons for b in h.benchmark_returns})
    for bench in benchmarks:
        table.add_row(
            bench,
            *[
                f"{h.benchmark_returns[bench] * 100:+.2f}%" if bench in h.benchmark_returns else "-"
                for h in result.horizons
            ],
        )
    console.print(table)
    console.print(
        f"Sharpe={result.sharpe:.2f}  Sortino={result.sortino:.2f}  "
        f"MaxDD={result.max_drawdown * 100:.2f}%  cost drag={result.cost_drag * 100:.3f}%"
        if result.sharpe is not None and result.sortino is not None and result.max_drawdown is not None
        else "insufficient history for risk metrics"
    )
    if result.contributors:
        top = sorted(result.contributors.items(), key=lambda kv: -kv[1])[:5]
        console.print("[bold]TOP CONTRIBUTORS[/bold] " + ", ".join(f"{t} {v * 100:+.2f}%" for t, v in top))
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    console.print(f"evaluation saved: {result.evaluation_id}")


def load_backtest_benchmarks() -> list[str]:
    from quant_platform.backtest.engine import load_backtest_config

    return list(load_backtest_config().benchmarks)
