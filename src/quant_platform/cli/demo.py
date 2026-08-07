"""`quantctl demo` — end-to-end offline run on synthetic data."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import EnvSettings, load_dotenv_if_present

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("demo")
    def demo(
        data_root: str = typer.Option("data", "--data-root", help="Artifact root."),
        seed: int = typer.Option(42, "--seed", help="RNG seed (reproducible)."),
    ) -> None:
        """Full pipeline on SYNTHETIC data with MockModelProvider. Nothing real."""
        load_dotenv_if_present()
        settings = EnvSettings.from_env()
        root = Path(data_root)
        audit = AuditLogger(root / "logs" / "audit.jsonl")

        console.print("[bold yellow]SYNTHETIC DATA + MOCK MODEL — offline demo, nothing real[/bold yellow]")
        from quant_platform.pipeline import run_demo

        summary = asyncio.run(run_demo(root, seed=seed, audit=audit))

        table = Table(title=f"demo run {summary['run_id']} (as of {summary['as_of_date']})")
        table.add_column("Stage")
        table.add_column("Outcome")
        table.add_row("data", f"{summary['bars_visible']} bars, {summary['news_visible']} news (gatekeeper-filtered)")
        table.add_row("evidence", f"{summary['evidence_cards']} cards")
        table.add_row("theses", str(summary["theses"]))
        table.add_row("ranking", summary["selection_rationale"])
        table.add_row(
            "portfolio",
            f"{summary['portfolio_positions']} positions, cash {summary['cash_weight']:.0%}",
        )
        table.add_row("snapshot", summary["snapshot_id"])
        bt = summary["backtest"]
        table.add_row(
            "backtest",
            f"cum {bt['cumulative_return']:+.2%}, sharpe {bt['sharpe']:.2f}, "
            f"maxDD {bt['max_drawdown']:+.2%}",
        )
        if summary["failure_record"]:
            table.add_row("failure analysis", summary["failure_record"])
        console.print(table)
        console.print(f"[dim]audit log: {audit.path} — {len(audit.read_all())} events[/dim]")
        console.print(f"[dim]dry_run={settings.dry_run} — no orders were placed[/dim]")
