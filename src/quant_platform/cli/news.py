"""`quantctl news ...` — NewsCatcher news intelligence (NEWS ONLY).

NewsCatcher provides news/intelligence ONLY — never prices, returns,
fundamentals or bars (market data comes from Bloomberg). The API key is
read from the environment and never printed.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from quant_platform.cli.research import _row
from quant_platform.core.config import EnvSettings, load_dotenv_if_present, load_yaml_config
from quant_platform.core.schemas import NewsArticle
from quant_platform.data.newscatcher import NewsCatcherError, NewsCatcherProvider

news_app = typer.Typer(help="NewsCatcher news intelligence (NEWS ONLY).", no_args_is_help=True)
console = Console()

_FOOTER = "news intelligence only — market data comes from Bloomberg"


def _trunc(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


async def _ping_and_close(provider: NewsCatcherProvider) -> tuple[str, str]:
    try:
        return await provider.ping()
    finally:
        await provider.aclose()


async def _search_and_close(
    provider: NewsCatcherProvider, query: str, start: date, end: date
) -> list[NewsArticle]:
    try:
        return await provider.search(query, start, end)
    finally:
        await provider.aclose()


@news_app.command("doctor")
def doctor() -> None:
    """NewsCatcher readiness: key configured, reachable + auth, cache writable."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()

    table = Table(title="quantctl news doctor — NewsCatcher readiness")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    failures = 0

    # 1./2. API key (never printed) + real ping (auth, minimal search, normalization)
    if not settings.newscatcher_configured:
        _row(
            table,
            "api key",
            "NOT_CONFIGURED",
            "NEWSCATCHER_API_KEY not set (.env locally) — key is never printed",
        )
        _row(table, "api ping", "SKIPPED", "no key — ping skipped")
        failures += 1
    else:
        _row(table, "api key", "PASS", "NEWSCATCHER_API_KEY present (not shown)")
        try:
            provider = NewsCatcherProvider(settings)
        except NewsCatcherError as exc:
            _row(table, "api ping", "FAIL", str(exc))
            failures += 1
        else:
            status, detail = asyncio.run(_ping_and_close(provider))
            _row(table, "api ping", status, detail)
            if status == "FAIL":
                failures += 1

    # 3. cache dir writable
    cache_dir = Path((load_yaml_config("news").get("cache") or {}).get("dir", "data/raw/news_cache"))
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _row(table, "cache dir", "PASS", f"{cache_dir} writable")
    except OSError as exc:
        _row(table, "cache dir", "FAIL", f"{cache_dir} not writable: {exc}")
        failures += 1

    console.print(table)
    console.print(f"[dim]{_FOOTER}[/dim]")
    if failures:
        raise typer.Exit(code=1)


@news_app.command("search")
def search(
    query: str = typer.Option(..., "--query", help="Search keywords, e.g. 'NVIDIA'."),
    limit: int = typer.Option(10, "--limit", help="Max rows to display."),
    days: int = typer.Option(7, "--days", help="Lookback window ending today."),
) -> None:
    """One real NewsCatcher search. NEWS ONLY — never market data."""
    load_dotenv_if_present()
    settings = EnvSettings.from_env()
    if not settings.newscatcher_configured:
        console.print("[red]NEWSCATCHER_API_KEY not set[/red] — set it in .env (never printed)")
        raise typer.Exit(code=1)

    end = date.today()
    start = end - timedelta(days=days)
    try:
        provider = NewsCatcherProvider(settings)
        articles = asyncio.run(_search_and_close(provider, query, start, end))
    except NewsCatcherError as exc:
        console.print(f"[red]NewsCatcher search failed honestly:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"news search: {query!r}  ({start} .. {end})")
    table.add_column("published")
    table.add_column("source")
    table.add_column("title")
    table.add_column("url")
    for article in articles[:limit]:
        table.add_row(
            article.published_at.date().isoformat(),
            article.source_domain,
            _trunc(article.title, 70),
            _trunc(article.url, 50),
        )
    console.print(table)
    console.print(f"[dim]{len(articles)} article(s) — {_FOOTER}[/dim]")
