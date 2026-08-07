"""`quantctl data ...` — data utilities (synthetic sample generation)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console

from quant_platform.data.sample_data import (
    SAMPLE_SOURCE_NOTE,
    generate_sample_export,
    generate_sample_news,
)

data_app = typer.Typer(help="Data utilities.", no_args_is_help=True)
console = Console()


@data_app.command("sample")
def sample(
    out: str = typer.Option("data/raw/bloomberg_exports", "--out", help="Output directory."),
    days: int = typer.Option(600, "--days", help="Calendar days of history ending today."),
    seed: int = typer.Option(42, "--seed", help="RNG seed (reproducible output)."),
) -> None:
    """Generate SYNTHETIC Bloomberg-style export CSVs + sample news."""
    console.print(f"[bold yellow]{SAMPLE_SOURCE_NOTE}[/bold yellow]")
    out_dir = Path(out)
    end = date.today()
    start = end - timedelta(days=days)
    prices = generate_sample_export(out_dir, start=start.isoformat(), end=end.isoformat(), seed=seed)
    news = generate_sample_news(out_dir, start=start.isoformat(), end=end.isoformat(), seed=seed)
    console.print(f"[green]sample prices[/green] -> {prices}")
    console.print(f"[green]sample news[/green]   -> {news}")
    console.print(f"[dim]README marker written to {out_dir / 'README.txt'}[/dim]")
