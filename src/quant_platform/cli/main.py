"""quantctl — single CLI entry point for the platform."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.core.config import (
    EnvSettings,
    load_all_configs,
    load_dotenv_if_present,
)

app = typer.Typer(
    name="quantctl",
    help="Quant research & PAPER-trading platform. NO LIVE TRADING.",
    no_args_is_help=True,
)
console = Console()

from quant_platform.cli.backtest import backtest_app  # noqa: E402
from quant_platform.cli.bloomberg import bloomberg_app  # noqa: E402
from quant_platform.cli.data import data_app  # noqa: E402
from quant_platform.cli.demo import register as register_demo  # noqa: E402
from quant_platform.cli.evaluate import evaluate_app  # noqa: E402
from quant_platform.cli.news import news_app  # noqa: E402
from quant_platform.cli.paper import paper_app  # noqa: E402
from quant_platform.cli.research import research_app  # noqa: E402

app.add_typer(backtest_app, name="backtest")
app.add_typer(bloomberg_app, name="bloomberg")
app.add_typer(data_app, name="data")
app.add_typer(evaluate_app, name="evaluate")
app.add_typer(news_app, name="news")
app.add_typer(paper_app, name="paper")
app.add_typer(research_app, name="research")
register_demo(app)


def _settings() -> EnvSettings:
    load_dotenv_if_present()
    return EnvSettings.from_env()


@app.command()
def doctor() -> None:
    """Environment doctor: config, safety defaults, directories, dependencies."""
    table = Table(title="quantctl doctor — system checks")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")

    failures = 0

    def check(name: str, fn) -> None:
        nonlocal failures
        try:
            status, detail = fn()
        except Exception as exc:  # honest diagnostics, never a crash
            status, detail = "FAIL", str(exc)
        if status == "FAIL":
            failures += 1
        table.add_row(name, status, detail)

    def _env() -> tuple[str, str]:
        s = _settings()
        return "PASS", f"trading_mode={s.trading_mode} dry_run={s.dry_run}"

    def _safety() -> tuple[str, str]:
        s = _settings()
        if s.trading_mode != "paper":
            return "FAIL", "TRADING_MODE must be 'paper'"
        if not s.dry_run:
            return "PASS", "DRY_RUN=false — paper orders may be submitted (paper only)"
        return "PASS", "dry-run default active; nothing will be submitted"

    def _configs() -> tuple[str, str]:
        from quant_platform.core.config import DEFAULT_CONFIG_DIR

        found = sorted(p.stem for p in DEFAULT_CONFIG_DIR.glob("*.yaml"))
        return ("PASS", f"loaded {len(found)} configs") if found else ("FAIL", f"no configs in {DEFAULT_CONFIG_DIR}")

    def _dirs() -> tuple[str, str]:
        from quant_platform.core.store import ArtifactStore

        store = ArtifactStore(_settings().data_root)
        return "PASS", f"data root ready at {store.root}"

    def _deps() -> tuple[str, str]:
        import importlib

        missing = [m for m in ("pandas", "pydantic", "pyarrow", "duckdb", "httpx", "yaml") if not _has(importlib, m)]
        return ("PASS", "core dependencies importable") if not missing else ("FAIL", f"missing: {missing}")

    def _optional() -> tuple[str, str]:
        import importlib.util

        blpapi = importlib.util.find_spec("blpapi") is not None
        ib = importlib.util.find_spec("ib_async") is not None or importlib.util.find_spec("ibapi") is not None
        return "PASS", f"blpapi={'yes' if blpapi else 'no (export fallback)'} ibkr_client={'yes' if ib else 'no'}"

    def _kimi() -> tuple[str, str]:
        s = _settings()
        if s.kimi_configured:
            return "PASS", f"KIMI_API_KEY present, model={s.kimi_model} (key not shown)"
        return (
            "NOT_CONFIGURED",
            "no KIMI_API_KEY — real research runs will fail; MockModelProvider "
            "covers only the offline demo/tests",
        )

    check("environment", _env)
    check("safety defaults", _safety)
    check("yaml configs", _configs)
    check("data directories", _dirs)
    check("core dependencies", _deps)
    check("optional adapters", _optional)
    check("kimi runtime", _kimi)

    console.print(table)
    console.print("[bold]RESEARCH / PAPER TRADING SYSTEM — NO LIVE TRADING[/bold]")
    if failures:
        raise typer.Exit(code=1)


def _has(importlib, module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False


config_app = typer.Typer(help="Configuration utilities.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("check")
def config_check() -> None:
    """Validate every YAML config loads and safety invariants hold."""
    settings = _settings()
    configs = load_all_configs()
    for name, data in configs.items():
        console.print(f"[green]OK[/green] configs/{name}.yaml ({len(data)} top-level keys)")
    scoring = configs["scoring"]
    total = sum(scoring["weights"].values())
    if abs(total - 1.0) > 1e-6:
        console.print(f"[red]FAIL[/red] scoring weights sum to {total}, expected 1.0")
        raise typer.Exit(code=1)
    console.print(f"[green]OK[/green] scoring weights sum to {total:.3f}")
    console.print(f"[green]OK[/green] trading_mode={settings.trading_mode} dry_run={settings.dry_run}")


@app.command()
def dashboard() -> None:
    """Launch the Streamlit dashboard (reads artifacts only)."""
    import importlib.util
    import subprocess

    if importlib.util.find_spec("streamlit") is None:
        console.print(
            "[red]streamlit not installed[/red] — install the 'dashboard' extra: "
            "pip install -e \".[dashboard]\""
        )
        raise typer.Exit(code=1)
    from quant_platform.dashboard import app as _app

    app_path = Path(_app.__file__)
    # sys.executable -m streamlit: never binds the wrong interpreter (Windows-safe)
    raise typer.Exit(code=subprocess.call([sys.executable, "-m", "streamlit", "run", str(app_path)]))


if __name__ == "__main__":
    sys.exit(app())
