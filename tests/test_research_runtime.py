"""Real research runtime tests: fully offline/mocked (export adapter + MockModelProvider).

Covers the same invariants as the demo integration test, plus the real-run
failure modes: no market data, no exported news, safety gate, PIT news
filtering, and the Kimi doctor ping (injected fake client, no network).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

from quant_platform.core.config import EnvSettings
from quant_platform.core.enums import SourceType
from quant_platform.core.schemas import PredictionSnapshot
from quant_platform.core.store import ArtifactStore
from quant_platform.core.timeutil import end_of_day_utc, start_of_day_utc
from quant_platform.data.bloomberg_export import BloombergExportAdapter
from quant_platform.data.sample_data import generate_sample_export, generate_sample_news
from quant_platform.models import MockModelProvider
from quant_platform.pipeline import _sector_label_map
from quant_platform.research_runtime import (
    ResearchRuntimeError,
    kimi_doctor_ping,
    load_exported_news,
    run_research,
)

TICKERS = ["NVDA", "AVGO", "MU", "SPY"]


def _settings(**overrides: Any) -> EnvSettings:
    return EnvSettings(kimi_api_key="test-key", **overrides)


def _make_export(export_dir: Path, seed: int = 42) -> tuple[date, date]:
    """Synthetic Bloomberg-style export covering [today-400d, today]."""
    end = date.today()
    start = end - timedelta(days=400)
    generate_sample_export(
        export_dir, tickers=TICKERS, start=start.isoformat(), end=end.isoformat(), seed=seed
    )
    return start, end


def _default_as_of(start: date, end: date, back: int = 80) -> date:
    return pd.bdate_range(start, end)[-back].date()


def _visible_news(news_dir: Path, as_of: date) -> list:
    ticker_to_label, _ = _sector_label_map()
    records = load_exported_news(news_dir, ticker_to_label)
    cutoff = end_of_day_utc(as_of)
    return [n for n in records if n.usable_from <= cutoff]


def _scripted(visible: list, as_of: date) -> dict[str, Any]:
    return {
        "evidence_extraction": {
            "cards": [
                {
                    "news_id": n.news_id,
                    "claim": n.headline.replace("[SYNTHETIC] ", "")[:120],
                    "category": "demand_signal",
                    "direction": "positive",
                    "confidence": 0.85,
                    "relevance": 0.8,
                    "securities": n.securities,
                    "sectors": n.sectors,
                }
                for n in visible
            ]
        },
        "sector": {
            "agent_name": "sector",
            "conclusion": "demand trend intact",
            "confidence": 0.85,
            "direction": "positive",
            "as_of_date": as_of.isoformat(),
        },
        "judge": {
            "agent_name": "judge",
            "conclusion": "bull case stronger on evidence",
            "confidence": 0.8,
            "direction": "positive",
            "as_of_date": as_of.isoformat(),
        },
    }


@pytest.fixture()
async def happy(tmp_path: Path):
    export_dir = tmp_path / "exports"
    start, end = _make_export(export_dir)
    generate_sample_news(
        export_dir,
        tickers=TICKERS,
        start=start.isoformat(),
        end=end.isoformat(),
        seed=42,
        per_ticker=6,
    )
    as_of = _default_as_of(start, end)
    visible = _visible_news(export_dir / "news", as_of)
    assert visible, "test setup: expected visible news before as_of"
    provider = MockModelProvider(scripted=_scripted(visible, as_of))
    summary = await run_research(
        tmp_path / "data",
        _settings(),
        as_of=as_of,
        tickers=TICKERS,
        market_adapter=BloombergExportAdapter(export_dir),
        provider=provider,
        news_dir=export_dir / "news",
    )
    return summary, visible, provider, ArtifactStore(tmp_path / "data")


class TestResearchRunHappyPath:
    async def test_completes_end_to_end(self, happy):
        summary, _, _, _ = happy
        assert summary["snapshot_id"]
        assert summary["bars_visible"] > 0
        assert summary["news_visible"] > 0
        assert summary["evidence_cards"] > 0
        assert summary["theses"] > 0
        assert summary["data_source"] == "bloomberg_export"
        assert summary["provider"] == "mock"

    async def test_snapshot_persisted(self, happy):
        summary, _, _, store = happy
        snap = store.load_model("snapshots", summary["snapshot_id"], PredictionSnapshot)
        assert snap.config_hash and snap.data_snapshot_hash
        assert snap.model_versions["data_source"] == "bloomberg_export"

    async def test_backtest_ran_on_test_window(self, happy):
        summary, _, _, _ = happy
        assert summary["backtest"] is not None
        assert "cumulative_return" in summary["backtest"]
        assert summary["test_window"][0] > summary["as_of_date"]

    async def test_pit_only_pre_cutoff_news_used(self, happy):
        summary, visible, _, _ = happy
        # every visible (<= as_of) news item entered the run — and nothing else
        assert summary["news_visible"] == len(visible)

    async def test_model_usage_accounted(self, happy):
        summary, _, provider, _ = happy
        assert summary["model_calls"] == provider.tracker.calls > 0
        assert summary["model_cost_usd"] == 0.0


class TestNewsAfterAsOfIsInvisible:
    async def test_future_news_never_enters_the_run(self, tmp_path: Path):
        export_dir = tmp_path / "exports"
        start, end = _make_export(export_dir)
        as_of = _default_as_of(start, end)
        past = as_of - timedelta(days=5)
        future = as_of + timedelta(days=5)
        news_dir = export_dir / "news"
        news_dir.mkdir(parents=True)
        pd.DataFrame(
            [
                {
                    "security": "NVDA US Equity",
                    "date": past.isoformat(),
                    "headline": "past item",
                    "body": "visible",
                },
                {
                    "security": "NVDA US Equity",
                    "date": future.isoformat(),
                    "headline": "future item",
                    "body": "must be invisible",
                },
            ]
        ).to_csv(news_dir / "news.csv", index=False)

        ticker_to_label, _ = _sector_label_map()
        records = load_exported_news(news_dir, ticker_to_label)
        future_id = next(n.news_id for n in records if n.published_at.date() == future)
        provider = MockModelProvider(scripted=_scripted(records, as_of))

        summary = await run_research(
            tmp_path / "data",
            _settings(),
            as_of=as_of,
            tickers=TICKERS,
            market_adapter=BloombergExportAdapter(export_dir),
            provider=provider,
            news_dir=news_dir,
        )
        assert summary["news_visible"] == 1  # only the past item
        extraction_prompts = [c.user_prompt for c in provider.calls if c.task == "evidence_extraction"]
        assert extraction_prompts
        assert all(future_id not in prompt for prompt in extraction_prompts)


class TestHonestFailures:
    async def test_no_news_dir_fails_clearly(self, tmp_path: Path):
        export_dir = tmp_path / "exports"
        start, end = _make_export(export_dir)
        with pytest.raises(ResearchRuntimeError, match="no Bloomberg news export"):
            await run_research(
                tmp_path / "data",
                _settings(),
                as_of=_default_as_of(start, end),
                tickers=TICKERS,
                market_adapter=BloombergExportAdapter(export_dir),
                provider=MockModelProvider(),
                news_dir=tmp_path / "missing_news",
            )

    async def test_empty_bars_fail_clearly(self, tmp_path: Path):
        class _EmptyAdapter:
            name = "stub_empty"

            def get_history(self, tickers, start, end, fields=None):
                return []

        with pytest.raises(ResearchRuntimeError, match="no data"):
            await run_research(
                tmp_path / "data",
                _settings(),
                tickers=TICKERS,
                market_adapter=_EmptyAdapter(),
                provider=MockModelProvider(),
            )

    async def test_safety_gate_refuses_non_dry_run(self, tmp_path: Path):
        with pytest.raises(ResearchRuntimeError, match="DRY_RUN"):
            await run_research(
                tmp_path / "data",
                _settings(dry_run=False),
                tickers=TICKERS,
                market_adapter=BloombergExportAdapter(tmp_path),
                provider=MockModelProvider(),
            )


class TestLoadExportedNews:
    def test_column_variants_and_normalization(self, tmp_path: Path):
        news_dir = tmp_path / "news"
        news_dir.mkdir()
        pd.DataFrame(
            [
                {
                    "ticker": "NVDA US Equity",
                    "published": "2024-06-03",
                    "title": "variant columns headline",
                    "story": "some body",
                },
                {"ticker": "AVGO", "published": "not-a-date", "title": "malformed row", "story": "skipped"},
            ]
        ).to_csv(news_dir / "exported.csv", index=False)
        stats: dict[str, int] = {}
        records = load_exported_news(news_dir, {"NVDA": "AI Infrastructure"}, stats=stats)
        assert len(records) == 1
        rec = records[0]
        assert rec.securities == ["NVDA"]  # " US Equity" suffix stripped
        assert rec.headline == "variant columns headline"
        assert rec.body == "some body"
        assert rec.source == SourceType.BLOOMBERG_EXPORT
        assert rec.source_ref.endswith("exported.csv")
        assert rec.usable_from == start_of_day_utc(date(2024, 6, 3))
        assert rec.published_at == rec.usable_from
        assert rec.sectors == ["AI Infrastructure"]
        assert stats["rows_skipped"] == 1  # malformed row counted, not hidden

    def test_missing_or_empty_dir_returns_empty(self, tmp_path: Path):
        assert load_exported_news(tmp_path / "nope", {}) == []
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_exported_news(empty, {}) == []


class _FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if body is not None else "<not json>"

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)

    async def post(self, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok_body() -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": "OK"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }


class TestKimiDoctorPing:
    async def test_pass_on_real_answer(self):
        client = _FakeClient([_FakeResponse(200, _ok_body())])
        status, detail = await kimi_doctor_ping(_settings(kimi_model="kimi-test"), client=client)
        assert status == "PASS"
        assert "model=kimi-test" in detail
        assert "tokens=4" in detail

    async def test_fail_is_honest_string_not_exception(self):
        client = _FakeClient([httpx.ConnectError("no route to host")])
        status, detail = await kimi_doctor_ping(_settings(kimi_model="kimi-test"), client=client)
        assert status == "FAIL"
        assert "no route to host" in detail
