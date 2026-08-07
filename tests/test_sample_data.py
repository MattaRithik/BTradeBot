"""Sample data generator: synthetic markers + end-to-end export ingestion."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from quant_platform.core.schemas import MarketBar
from quant_platform.data.bloomberg_export import BloombergExportAdapter
from quant_platform.data.sample_data import (
    SAMPLE_SOURCE_NOTE,
    generate_sample_export,
    generate_sample_news,
)

START = "2024-01-01"
END = "2024-06-30"


def test_generated_files_and_markers(tmp_path: Path) -> None:
    prices = generate_sample_export(tmp_path, tickers=["NVDA", "SPY"], start=START, end=END, seed=7)
    news = generate_sample_news(tmp_path, tickers=["NVDA"], start=START, end=END, seed=7)

    assert prices.exists()
    assert news.exists()
    assert news.parent.name == "news"  # kept out of the export adapter's inbox

    readme = (tmp_path / "README.txt").read_text(encoding="utf-8")
    assert SAMPLE_SOURCE_NOTE in readme

    df = pd.read_csv(news)
    assert not df.empty
    assert df["headline"].str.startswith("[SYNTHETIC]").all()

    prices_df = pd.read_csv(prices)
    assert set(prices_df.columns) == {
        "security", "date", "PX_OPEN", "PX_HIGH", "PX_LOW", "PX_LAST", "PX_VOLUME", "CUR_MKT_CAP",
    }
    assert set(prices_df["security"].unique()) == {"NVDA US Equity", "SPY US Equity"}


def test_export_adapter_ingests_generated_prices(tmp_path: Path) -> None:
    generate_sample_export(tmp_path, tickers=["NVDA", "AMD"], start=START, end=END, seed=3)
    generate_sample_news(tmp_path, tickers=["NVDA"], start=START, end=END, seed=3)

    adapter = BloombergExportAdapter(tmp_path)
    bars = adapter.get_history(["NVDA", "AMD"], date(2024, 3, 1), date(2024, 3, 31))

    assert bars, "adapter must return bars for the generated export"
    assert all(isinstance(b, MarketBar) for b in bars)
    assert {b.ticker for b in bars} == {"NVDA", "AMD"}
    assert all(b.raw_security == f"{b.ticker} US Equity" for b in bars)
    assert all(date(2024, 3, 1) <= b.timestamp.date() <= date(2024, 3, 31) for b in bars)
    assert all(b.low <= b.open <= b.high and b.low <= b.close <= b.high for b in bars)


def test_generation_is_reproducible(tmp_path: Path) -> None:
    a = generate_sample_export(tmp_path / "a", tickers=["NVDA"], start=START, end=END, seed=11)
    b = generate_sample_export(tmp_path / "b", tickers=["NVDA"], start=START, end=END, seed=11)
    assert a.read_bytes() == b.read_bytes()
