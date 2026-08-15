"""BarStore + CachingMarketProvider: resumable Bloomberg history cache."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import dt, make_bar
from quant_platform.data.barstore import BarStore, CachingMarketProvider


@pytest.fixture()
def store(tmp_path):
    return BarStore(tmp_path / "bloomberg")


def _bars(ticker: str, days: list[date]) -> list:
    return [make_bar(ticker=ticker, ts=dt(d.year, d.month, d.day, 20)) for d in days]


class TestBarStore:
    def test_write_read_roundtrip(self, store):
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        added = store.write("NVDA", _bars("NVDA", days), provider="test")
        assert added == 3
        out = store.read("NVDA", date(2024, 1, 1), date(2024, 1, 31))
        assert len(out) == 3
        assert out[0].ticker == "NVDA"

    def test_merge_dedups_and_counts_new_rows(self, store):
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        store.write("NVDA", _bars("NVDA", days))
        added = store.write("NVDA", _bars("NVDA", [date(2024, 1, 3), date(2024, 1, 4)]))
        assert added == 1
        assert store.coverage("NVDA") == (date(2024, 1, 2), date(2024, 1, 4))

    def test_coverage_none_when_absent(self, store):
        assert store.coverage("NOPE") is None
        assert store.read("NOPE", date(2024, 1, 1), date(2024, 2, 1)) == []

    def test_manifest_updated(self, store):
        store.write("NVDA", _bars("NVDA", [date(2024, 1, 2)]), provider="test")
        manifest = store.manifest()
        entry = manifest["tickers"]["NVDA"]
        assert entry["rows"] == 1
        assert entry["provider"] == "test"
        assert entry["retrieved_at"]


class SpyInner:
    name = "spy_live"

    def __init__(self, bars):
        self._bars = bars
        self.calls: list[tuple] = []

    def get_history(self, tickers, start, end, fields=None):
        self.calls.append((list(tickers), start, end))
        return [b for b in self._bars if b.ticker in tickers]


class TestCachingMarketProvider:
    def test_covered_range_served_without_live_call(self, store):
        store.write("NVDA", _bars("NVDA", [date(2024, 1, 2), date(2024, 1, 3)]))
        spy = SpyInner([])
        provider = CachingMarketProvider(store, inner=spy)
        bars = provider.get_history(["NVDA"], date(2024, 1, 1), date(2024, 1, 4))
        assert len(bars) == 2
        assert spy.calls == []

    def test_missing_ticker_fetched_once_then_cached(self, store):
        spy = SpyInner(_bars("AMD", [date(2024, 1, 2)]))
        provider = CachingMarketProvider(store, inner=spy)
        provider.get_history(["AMD"], date(2024, 1, 1), date(2024, 1, 3))
        provider.get_history(["AMD"], date(2024, 1, 1), date(2024, 1, 3))
        assert len(spy.calls) == 1  # second call served from the store

    def test_no_live_adapter_is_honest_error(self, store):
        provider = CachingMarketProvider(store, inner=None)
        with pytest.raises(ConnectionError, match="bloomberg sync"):
            provider.get_history(["TSLA"], date(2024, 1, 1), date(2024, 2, 1))

    def test_live_adapter_empty_result_is_honest_error(self, store):
        provider = CachingMarketProvider(store, inner=SpyInner([]))
        with pytest.raises(ConnectionError, match="no bars"):
            provider.get_history(["TSLA"], date(2024, 1, 1), date(2024, 2, 1))
