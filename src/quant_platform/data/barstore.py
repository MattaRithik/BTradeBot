"""Persistent, resumable local cache of Bloomberg daily bars.

Bloomberg Terminal time is scarce: ``quantctl bloomberg sync`` downloads the
configured universe ONCE into this store, and later research/backtest runs
read from it without re-pulling immutable history. Bars are stored as one
parquet per ticker under ``<root>/bars/`` plus a human-inspectable
``manifest.json`` with per-ticker coverage, row counts and retrieval time.

PIT safety: the store is a plain cache — it never decides visibility. Every
read still flows through PITRepository/TimeGatekeeper, so a cached bar after
the cutoff cannot leak into a research run.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from quant_platform.core.schemas import MarketBar
from quant_platform.core.timeutil import utc_now

_SAFE = re.compile(r"[^A-Z0-9_.-]")


def _fname(ticker: str) -> str:
    return _SAFE.sub("_", ticker.upper()) + ".parquet"


class BarStore:
    """One parquet per ticker + a manifest; merge-on-write, dedup by date."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.bars_dir = self.root / "bars"
        self.bars_dir.mkdir(parents=True, exist_ok=True)

    # -- io ------------------------------------------------------------------
    def _path(self, ticker: str) -> Path:
        return self.bars_dir / _fname(ticker)

    def tickers(self) -> list[str]:
        return sorted(p.stem for p in self.bars_dir.glob("*.parquet"))

    def coverage(self, ticker: str) -> tuple[date, date] | None:
        path = self._path(ticker)
        if not path.exists():
            return None
        df = pd.read_parquet(path, columns=["timestamp"])
        if df.empty:
            return None
        days = pd.to_datetime(df["timestamp"], utc=True).dt.date
        return min(days), max(days)

    def read(self, ticker: str, start: date, end: date) -> list[MarketBar]:
        path = self._path(ticker)
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        if df.empty:
            return []
        days = pd.to_datetime(df["timestamp"], utc=True).dt.date
        df = df[(days >= start) & (days <= end)]
        return [MarketBar.model_validate(rec) for rec in df.to_dict("records")]

    def write(self, ticker: str, bars: list[MarketBar], provider: str = "") -> int:
        """Merge bars for one ticker. Returns the number of NEW rows added."""
        if not bars:
            return 0
        new = pd.DataFrame([b.model_dump(mode="json") for b in bars])
        new["timestamp"] = pd.to_datetime(new["timestamp"], utc=True)
        path = self._path(ticker)
        if path.exists():
            old = pd.read_parquet(path)
            old["timestamp"] = pd.to_datetime(old["timestamp"], utc=True)
            combined = pd.concat([old, new], ignore_index=True)
        else:
            combined = new
        before = len(pd.read_parquet(path)) if path.exists() else 0
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last").sort_values(
            "timestamp"
        )
        combined.to_parquet(path, index=False)
        added = len(combined) - before
        self._update_manifest(ticker, combined, provider)
        return added

    # -- manifest --------------------------------------------------------------
    def _update_manifest(self, ticker: str, df: pd.DataFrame, provider: str) -> None:
        manifest = self.manifest()
        days = df["timestamp"].dt.date
        manifest["tickers"][ticker.upper()] = {
            "first_date": min(days).isoformat(),
            "last_date": max(days).isoformat(),
            "rows": len(df),
            "provider": provider,
            "retrieved_at": utc_now().isoformat(),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "manifest.tmp"
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.root / "manifest.json")

    def manifest(self) -> dict[str, Any]:
        path = self.root / "manifest.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"tickers": {}}


class CachingMarketProvider:
    """Cache-first MarketDataProvider over a BarStore.

    Covered ranges are served from the store without touching the terminal.
    Missing tickers/ranges are fetched from the live adapter (when given) and
    merged into the store; without a live adapter the miss is an honest error
    naming the sync command — never fabricated data.
    """

    name = "bloomberg_cache"

    def __init__(self, store: BarStore, inner: Any = None) -> None:
        self.store = store
        self.inner = inner  # live adapter (desktop facade) or None

    def get_history(
        self, tickers: list[str], start: date, end: date, fields: list[str] | None = None
    ) -> list[MarketBar]:
        missing = [t for t in tickers if not self._covers(t, start, end)]
        if missing and self.inner is not None:
            fetched = self.inner.get_history(missing, start, end)
            by_ticker: dict[str, list[MarketBar]] = {}
            for bar in fetched:
                by_ticker.setdefault(bar.ticker, []).append(bar)
            for ticker, bars in by_ticker.items():
                self.store.write(ticker, bars, provider=getattr(self.inner, "name", "live"))
            still_missing = [t for t in missing if t.upper() not in {k.upper() for k in by_ticker}]
            if still_missing:
                raise ConnectionError(
                    f"live adapter returned no bars for {still_missing} "
                    f"({start}..{end}); partial errors: {getattr(self.inner, 'partial_errors', [])}"
                )
        elif missing:
            raise ConnectionError(
                f"bar store has no coverage for {missing} over {start}..{end} — "
                "run `quantctl bloomberg sync` on the Bloomberg machine first"
            )
        bars: list[MarketBar] = []
        for t in tickers:
            bars.extend(self.store.read(t, start, end))
        return bars

    def _covers(self, ticker: str, start: date, end: date) -> bool:
        cov = self.store.coverage(ticker)
        if cov is None:
            return False
        c0, c1 = cov
        # tolerate weekend/holiday gaps at both window edges
        return c0 <= start + timedelta(days=7) and c1 >= end - timedelta(days=4)


def sync_universe() -> list[str]:
    """Every ticker the platform can request: universe securities + ETFs + benchmarks."""
    from quant_platform.core.config import load_yaml_config

    tickers: set[str] = set()
    universe = load_yaml_config("universe").get("universe", {})
    for entry in universe.values():
        tickers.update(entry.get("securities", []))
        tickers.update(entry.get("etfs", []))
    tickers.update(load_yaml_config("universe").get("college_test_universe", []))
    bench = load_yaml_config("benchmarks")
    tickers.update(bench.get("primary", []))
    return sorted(tickers)
