"""`quantctl bloomberg ...` — diagnostics and sample pulls."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.config import EnvSettings, load_dotenv_if_present, load_yaml_config
from quant_platform.data.bloomberg_desktop import BloombergDesktopAdapter
from quant_platform.data.bloomberg_export import BloombergExportAdapter
from quant_platform.data.providers import ProviderDiagnostics

bloomberg_app = typer.Typer(help="Bloomberg data layer.", no_args_is_help=True)
console = Console()


def _render(diag: ProviderDiagnostics) -> None:
    table = Table(title=f"provider: {diag.provider} (available={diag.available})")
    table.add_column("Capability")
    table.add_column("Status")
    table.add_column("Detail")
    for c in diag.checks:
        style = {"PASS": "green", "FAIL": "red", "NOT_ENTITLED": "yellow", "SKIPPED": "dim"}.get(c.status, "")
        table.add_row(c.capability, f"[{style}]{c.status}[/{style}]", c.detail)
    console.print(table)


@bloomberg_app.command("doctor")
def doctor(
    export_only: bool = typer.Option(False, "--export-only", help="Skip BLPAPI probes."),
) -> None:
    """Probe Bloomberg connectivity/entitlements. Honest PASS/FAIL/NOT ENTITLED."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    cfg = load_yaml_config("bloomberg")

    desktop = BloombergDesktopAdapter(host=settings.bloomberg_host, port=settings.bloomberg_port)
    if export_only:
        console.print("[dim]BLPAPI probes skipped (--export-only)[/dim]")
        exit_code = 0
    else:
        diag = desktop.diagnose()
        _render(diag)
        exit_code = 0 if diag.available else 1
        if not desktop.package_available:
            console.print(
                "[yellow]blpapi not installed — this is expected off-terminal. "
                "Use the export adapter.[/yellow]"
            )

    export = BloombergExportAdapter(Path(cfg["export"]["inbox"]))
    export_diag = export.diagnose()
    _render(export_diag)
    if not export_diag.available:
        console.print(
            f"[dim]Export inbox empty or missing: {cfg['export']['inbox']} — "
            "drop Bloomberg CSV/XLSX exports there.[/dim]"
        )
    raise typer.Exit(code=exit_code)


@bloomberg_app.command("sample")
def sample(
    days: int = typer.Option(120, help="History length ending today."),
    export_only: bool = typer.Option(False, "--export-only"),
) -> None:
    """Pull the tiny college test universe and save normalized bars."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    cfg = load_yaml_config("bloomberg")
    universe = load_yaml_config("universe")["college_test_universe"]
    end = date.today()
    start = end - timedelta(days=days)

    from quant_platform.core.store import ArtifactStore

    store = ArtifactStore(settings.data_root)
    bars = []
    if not export_only:
        desktop = BloombergDesktopAdapter(host=settings.bloomberg_host, port=settings.bloomberg_port)
        if desktop.package_available:
            try:
                securities = [f"{t} US Equity" for t in universe]
                bars = desktop.get_history(securities, start, end)
                console.print(f"[green]BLPAPI[/green] returned {len(bars)} bars")
            except Exception as exc:
                console.print(f"[red]BLPAPI failed honestly:[/red] {exc}")
        else:
            console.print("[yellow]blpapi unavailable — trying export adapter[/yellow]")
    if not bars:
        export = BloombergExportAdapter(Path(cfg["export"]["inbox"]))
        try:
            bars = export.get_history(universe, start, end)
            console.print(f"[green]export adapter[/green] returned {len(bars)} bars")
        except Exception as exc:
            console.print(f"[red]export adapter failed honestly:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    import pandas as pd

    df = pd.DataFrame([b.model_dump(mode="json") for b in bars])
    path = store.save_table("normalized", f"sample_bars_{end.isoformat()}", df)
    console.print(f"saved {len(df)} bars -> {path}")
