"""Research-runtime + NewsCatcher integration tests: fully offline/mocked.

Harness mirrors tests/test_research_runtime.py (synthetic export bars/news +
MockModelProvider); the news side additionally injects a MockNewsProvider so
the dual-source flow (NewsCatcher primary + Bloomberg export), the failure
policy, cross-source dedup, and pipeline-level PIT enforcement are covered.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import quant_platform.research_runtime as research_runtime
from quant_platform.core.config import EnvSettings
from quant_platform.core.enums import SourceType
from quant_platform.core.schemas import NewsArticle, NewsRecord, PredictionSnapshot
from quant_platform.core.store import ArtifactStore
from quant_platform.core.timeutil import end_of_day_utc
from quant_platform.data.bloomberg_export import BloombergExportAdapter
from quant_platform.data.newscatcher import MockNewsProvider, NewsCatcherError
from quant_platform.data.sample_data import generate_sample_export, generate_sample_news
from quant_platform.models import MockModelProvider
from quant_platform.pipeline import _sector_label_map
from quant_platform.research_runtime import (
    ResearchRuntimeError,
    load_exported_news,
    run_research,
)

TICKERS = ["NVDA", "AVGO", "MU", "SPY"]

NEWS_CFG: dict[str, Any] = {
    "provider": {"max_articles_per_run": 300},
    "windows": {"company_days": 30, "sector_days": 30, "macro_days": 14, "chunk_days": 31},
    "company_aliases": {
        "NVDA": ["NVIDIA", "NVIDIA Corporation"],
        "MU": ["Micron", "Micron Technology"],
    },
    "sector_queries": {},
    "macro_themes": [],
    "reputable_domains": [],
    "on_primary_failure": "degrade",
    "cache": {"enabled": False},
}


def _settings(**overrides: Any) -> EnvSettings:
    return EnvSettings(kimi_api_key="test-key", **overrides)


def _make_export(export_dir: Path, seed: int = 42) -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=400)
    generate_sample_export(
        export_dir, tickers=TICKERS, start=start.isoformat(), end=end.isoformat(), seed=seed
    )
    return start, end


def _default_as_of(start: date, end: date, back: int = 80) -> date:
    return pd.bdate_range(start, end)[-back].date()


def _visible_news(news_dir: Path, as_of: date) -> list[NewsRecord]:
    ticker_to_label, _ = _sector_label_map()
    records = load_exported_news(news_dir, ticker_to_label)
    cutoff = end_of_day_utc(as_of)
    return [n for n in records if n.usable_from <= cutoff]


def _dt(d: date, hh: int = 10) -> datetime:
    return datetime(d.year, d.month, d.day, hh, tzinfo=UTC)


def _nc_article(article_id: str, title: str, published: datetime) -> NewsArticle:
    return NewsArticle(
        article_id=article_id,
        provider="newscatcher",
        published_at=published,
        retrieved_at=published,
        title=title,
        summary=title,
        source_domain="reuters.com",
        url=f"https://reuters.com/{article_id}",
        content_hash=f"hash_{article_id}",
    )


def _nc_record(
    article_id: str, title: str, published: datetime, securities: list[str], sectors: list[str]
) -> NewsRecord:
    """The NewsRecord the runtime will build for the article above (for scripting cards)."""
    return NewsRecord(
        news_id=f"nc_{article_id}",
        source=SourceType.NEWSCATCHER,
        source_ref=f"https://reuters.com/{article_id}",
        headline=title,
        body=title,
        securities=securities,
        sectors=sectors,
        published_at=published,
        usable_from=published,
        retrieved_at=published,
    )


def _scripted(records: list[NewsRecord], as_of: date) -> dict[str, Any]:
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
                for n in records
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
def recorded_cards(monkeypatch: pytest.MonkeyPatch) -> list:
    """Capture every EvidenceCard the pipeline's engine produces."""
    seen: list = []
    base = research_runtime.EvidenceEngine

    class _Recording(base):
        async def extract(self, news, as_of_date):
            cards = await super().extract(news, as_of_date)
            seen.extend(cards)
            return cards

    monkeypatch.setattr(research_runtime, "EvidenceEngine", _Recording)
    return seen


def _export_with_news(tmp_path: Path, per_ticker: int = 6) -> tuple[Path, date]:
    export_dir = tmp_path / "exports"
    start, end = _make_export(export_dir)
    generate_sample_news(
        export_dir,
        tickers=TICKERS,
        start=start.isoformat(),
        end=end.isoformat(),
        seed=42,
        per_ticker=per_ticker,
    )
    return export_dir, _default_as_of(start, end)


class TestDualSourceHappyPath:
    async def test_both_sources_flow_with_provenance(self, tmp_path: Path, recorded_cards: list):
        export_dir, as_of = _export_with_news(tmp_path)
        visible = _visible_news(export_dir / "news", as_of)
        past = _dt(as_of - timedelta(days=3))
        articles = [
            _nc_article("nca1", "NVIDIA Corporation reports record data center revenue", past),
            _nc_article("nca2", "Micron Technology ramps HBM production", past),
        ]
        nc_records = [
            _nc_record("nca1", articles[0].title, past, ["NVDA"], ["AI Infrastructure"]),
            _nc_record("nca2", articles[1].title, past, ["MU"], ["Memory & Storage"]),
        ]
        provider = MockModelProvider(scripted=_scripted([*visible, *nc_records], as_of))

        summary = await run_research(
            tmp_path / "data",
            _settings(),
            as_of=as_of,
            tickers=TICKERS,
            market_adapter=BloombergExportAdapter(export_dir),
            provider=provider,
            news_dir=export_dir / "news",
            news_provider=MockNewsProvider(articles=articles),
            news_config=NEWS_CFG,
        )

        assert summary["news_sources"] == {"newscatcher": 2, "bloomberg_export": len(visible)}
        assert summary["news_visible"] == len(visible) + 2
        assert summary["newscatcher"]["returned"] == 2
        assert summary["newscatcher"]["queries_run"] == len(TICKERS)  # company queries only
        assert summary["snapshot_id"]
        assert summary["backtest"] is not None
        assert summary["evidence_cards"] > 0
        # provenance per provider, on the actual cards the pipeline produced
        card_sources = {c.source for c in recorded_cards}
        assert SourceType.NEWSCATCHER in card_sources
        assert SourceType.BLOOMBERG_EXPORT in card_sources
        snap = ArtifactStore(tmp_path / "data").load_model(
            "snapshots", summary["snapshot_id"], PredictionSnapshot
        )
        assert snap.model_versions["news_providers"] == "newscatcher,bloomberg_export"

    async def test_newscatcher_only_run(self, tmp_path: Path):
        export_dir, as_of = _export_with_news(tmp_path)
        past = _dt(as_of - timedelta(days=2))
        articles = [_nc_article("solo1", "NVIDIA Corporation unveils new accelerator", past)]
        records = [_nc_record("solo1", articles[0].title, past, ["NVDA"], ["AI Infrastructure"])]
        provider = MockModelProvider(scripted=_scripted(records, as_of))

        summary = await run_research(
            tmp_path / "data",
            _settings(),
            as_of=as_of,
            tickers=TICKERS,
            market_adapter=BloombergExportAdapter(export_dir),
            provider=provider,
            news_dir=tmp_path / "missing_news",  # no export news at all
            news_provider=MockNewsProvider(articles=articles),
            news_config=NEWS_CFG,
        )
        assert summary["news_sources"] == {"newscatcher": 1, "bloomberg_export": 0}
        assert summary["news_visible"] == 1
        assert summary["evidence_cards"] == 1


class TestOutagePolicy:
    async def test_degrade_continues_on_export_news(self, tmp_path: Path):
        export_dir, as_of = _export_with_news(tmp_path)
        visible = _visible_news(export_dir / "news", as_of)
        provider = MockModelProvider(scripted=_scripted(visible, as_of))

        summary = await run_research(
            tmp_path / "data",
            _settings(),
            as_of=as_of,
            tickers=TICKERS,
            market_adapter=BloombergExportAdapter(export_dir),
            provider=provider,
            news_dir=export_dir / "news",
            news_provider=MockNewsProvider(fail_with=NewsCatcherError("api down")),
            news_config=NEWS_CFG,
        )
        assert summary["news_sources"] == {"newscatcher": 0, "bloomberg_export": len(visible)}
        assert any("NewsCatcher FAILED" in w for w in summary["warnings"])
        assert summary["snapshot_id"]  # the run still completes honestly

    async def test_fail_policy_aborts(self, tmp_path: Path):
        export_dir, as_of = _export_with_news(tmp_path)
        visible = _visible_news(export_dir / "news", as_of)
        cfg = {**NEWS_CFG, "on_primary_failure": "fail"}
        with pytest.raises(ResearchRuntimeError, match="on_primary_failure=fail"):
            await run_research(
                tmp_path / "data",
                _settings(),
                as_of=as_of,
                tickers=TICKERS,
                market_adapter=BloombergExportAdapter(export_dir),
                provider=MockModelProvider(scripted=_scripted(visible, as_of)),
                news_dir=export_dir / "news",
                news_provider=MockNewsProvider(fail_with=NewsCatcherError("api down")),
                news_config=cfg,
            )

    async def test_no_news_at_all_fails_never_fabricates(self, tmp_path: Path):
        export_dir, as_of = _export_with_news(tmp_path)
        with pytest.raises(ResearchRuntimeError, match="no Bloomberg news export"):
            await run_research(
                tmp_path / "data",
                _settings(),
                as_of=as_of,
                tickers=TICKERS,
                market_adapter=BloombergExportAdapter(export_dir),
                provider=MockModelProvider(),
                news_dir=tmp_path / "missing_news",
                news_provider=MockNewsProvider(fail_with=NewsCatcherError("api down")),
                news_config=NEWS_CFG,
            )


class TestPipelinePIT:
    async def test_future_article_never_enters_the_run(self, tmp_path: Path, recorded_cards: list):
        export_dir, as_of = _export_with_news(tmp_path)
        past = _dt(as_of - timedelta(days=2))
        future = _dt(as_of + timedelta(days=2))
        articles = [
            _nc_article("past1", "NVIDIA Corporation beats on data center demand", past),
            _nc_article("future1", "NVIDIA Corporation secret future announcement", future),
        ]
        records = [_nc_record("past1", articles[0].title, past, ["NVDA"], ["AI Infrastructure"])]
        provider = MockModelProvider(scripted=_scripted(records, as_of))

        summary = await run_research(
            tmp_path / "data",
            _settings(),
            as_of=as_of,
            tickers=TICKERS,
            market_adapter=BloombergExportAdapter(export_dir),
            provider=provider,
            news_dir=tmp_path / "missing_news",
            news_provider=MockNewsProvider(articles=articles),
            news_config=NEWS_CFG,
        )
        assert summary["news_visible"] == 1  # only the <= cutoff article
        assert summary["newscatcher"]["future_dropped"] == 1
        assert all(c.source_ref != "nc_future1" for c in recorded_cards)
        extraction_prompts = [c.user_prompt for c in provider.calls if c.task == "evidence_extraction"]
        assert extraction_prompts
        assert all("nc_future1" not in prompt for prompt in extraction_prompts)


class TestCrossSourceDedup:
    async def test_same_story_counted_once_newscatcher_wins(self, tmp_path: Path):
        export_dir, as_of = _export_with_news(tmp_path, per_ticker=0)
        story_date = as_of - timedelta(days=3)
        news_dir = export_dir / "news"
        news_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            [
                {
                    "security": "NVDA US Equity",
                    "date": story_date.isoformat(),
                    "headline": "NVIDIA unveils new GPU",
                    "body": "terminal export version",
                }
            ]
        ).to_csv(news_dir / "news.csv", index=False)

        # same normalized headline + same published date from NewsCatcher
        articles = [_nc_article("dup1", "NVIDIA unveils new GPU", _dt(story_date, 15))]
        records = [
            _nc_record("dup1", "NVIDIA unveils new GPU", _dt(story_date, 15), ["NVDA"], ["AI Infrastructure"])
        ]
        provider = MockModelProvider(scripted=_scripted(records, as_of))

        summary = await run_research(
            tmp_path / "data",
            _settings(),
            as_of=as_of,
            tickers=TICKERS,
            market_adapter=BloombergExportAdapter(export_dir),
            provider=provider,
            news_dir=news_dir,
            news_provider=MockNewsProvider(articles=articles),
            news_config=NEWS_CFG,
        )
        # one story, counted once — the NewsCatcher record survives (richer provenance),
        # proven by the nc_-citing card resolving through the engine
        assert summary["news_visible"] == 1
        assert summary["news_sources"] == {"newscatcher": 1, "bloomberg_export": 0}
        assert summary["evidence_cards"] == 1
