"""News-intel orchestration tests: deterministic plan/dedup/match/rank/PIT.

All offline. Point-in-time enforcement is tested both against a mock
provider and against a cache pre-seeded with future articles — the
gatekeeper runs AFTER retrieval, so a cached response from a different run
can never leak future news into THIS run.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

from quant_platform.core.config import EnvSettings
from quant_platform.core.decision import DecisionClock
from quant_platform.core.enums import SourceType
from quant_platform.core.gatekeeper import ResearchContext, TimeGatekeeper
from quant_platform.core.schemas import NewsArticle
from quant_platform.data.newscatcher import MockNewsProvider, NewsCatcherProvider
from quant_platform.research.news_intel import (
    NewsQuery,
    build_query_plan,
    canonical_url,
    chunk_date_range,
    deduplicate,
    gather_news,
    match_article,
    normalize_title,
    rank_articles,
    to_news_record,
)

AS_OF = date(2024, 12, 31)
CUTOFF = DecisionClock().cutoff_for(AS_OF)  # 16:15 ET == 21:15 UTC

NEWS_CFG: dict[str, Any] = {
    "provider": {"max_articles_per_run": 300},
    "windows": {"company_days": 30, "sector_days": 30, "macro_days": 14, "chunk_days": 31},
    "company_aliases": {
        "NVDA": ["NVIDIA", "NVIDIA Corporation"],
        "AMD": ["AMD", "Advanced Micro Devices"],
        "MU": ["Micron", "Micron Technology"],
    },
    "sector_queries": {
        "AI Infrastructure": ["artificial intelligence infrastructure", "GPUs"],
    },
    "macro_themes": [
        {
            "id": "export_controls",
            "query": "semiconductor export controls China",
            "sectors": ["AI Infrastructure"],
        },
    ],
    "reputable_domains": ["reuters.com"],
}

TICKER_TO_LABEL = {"NVDA": "AI Infrastructure", "AMD": "CPU / Inference", "MU": "Memory & Storage"}


def dt(y: int, m: int, d: int, hh: int = 12, minute: int = 0) -> datetime:
    return datetime(y, m, d, hh, minute, tzinfo=UTC)


def make_article(article_id: str = "a1", **overrides: Any) -> NewsArticle:
    fields: dict[str, Any] = {
        "article_id": article_id,
        "provider": "newscatcher",
        "published_at": dt(2024, 12, 30),
        "retrieved_at": dt(2024, 12, 31),
        "title": f"headline {article_id}",
        "content_hash": f"hash_{article_id}",
    }
    fields.update(overrides)
    return NewsArticle(**fields)


def gate_for(as_of: date = AS_OF) -> TimeGatekeeper:
    context = ResearchContext(
        run_id="news_test",
        as_of_date=as_of,
        visible_start=date(2023, 1, 1),
        visible_end=as_of,
    )
    return TimeGatekeeper(context=context)


class TestQueryPlan:
    def test_deterministic(self):
        plan1 = build_query_plan(["NVDA", "AMD"], TICKER_TO_LABEL, AS_OF, NEWS_CFG)
        plan2 = build_query_plan(["NVDA", "AMD"], TICKER_TO_LABEL, AS_OF, NEWS_CFG)
        assert plan1 == plan2

    def test_company_query_ors_quoted_aliases(self):
        (nvda,) = [
            q for q in build_query_plan(["NVDA"], TICKER_TO_LABEL, AS_OF, NEWS_CFG) if q.kind == "company"
        ]
        assert nvda.query == '"NVIDIA" OR "NVIDIA Corporation"'
        assert nvda.label == "NVDA"
        assert nvda.sectors == ["AI Infrastructure"]
        assert nvda.from_date == AS_OF - timedelta(days=30)
        assert nvda.to_date == AS_OF

    def test_sector_query_or_combined(self):
        (sector,) = [
            q for q in build_query_plan(["NVDA"], TICKER_TO_LABEL, AS_OF, NEWS_CFG) if q.kind == "sector"
        ]
        assert sector.query == "artificial intelligence infrastructure OR GPUs"
        assert sector.label == "AI Infrastructure"
        assert sector.sectors == ["AI Infrastructure"]

    def test_macro_theme_with_sector_mapping(self):
        (macro,) = [
            q for q in build_query_plan(["NVDA"], TICKER_TO_LABEL, AS_OF, NEWS_CFG) if q.kind == "macro"
        ]
        assert macro.kind == "macro"
        assert macro.label == "export_controls"
        assert macro.query == "semiconductor export controls China"
        assert macro.sectors == ["AI Infrastructure"]
        assert macro.from_date == AS_OF - timedelta(days=14)

    def test_unknown_ticker_fallback_quoted_keyword(self):
        (fallback,) = [
            q for q in build_query_plan(["XYZ"], TICKER_TO_LABEL, AS_OF, NEWS_CFG) if q.kind == "company"
        ]
        assert fallback.query == '"XYZ"'
        assert fallback.sectors == []

    def test_alias_lookup_case_insensitive(self):
        (nvda,) = [
            q for q in build_query_plan(["nvda"], TICKER_TO_LABEL, AS_OF, NEWS_CFG) if q.kind == "company"
        ]
        assert "NVIDIA" in nvda.query


class TestChunkDateRange:
    def test_long_range_chunked_contiguously(self):
        chunks = chunk_date_range(date(2019, 1, 1), date(2019, 3, 15), 31)
        assert chunks == [
            (date(2019, 1, 1), date(2019, 1, 31)),
            (date(2019, 2, 1), date(2019, 3, 3)),
            (date(2019, 3, 4), date(2019, 3, 15)),
        ]
        for (_, prev_end), (next_start, _) in pairwise(chunks):
            assert next_start == prev_end + timedelta(days=1)  # no overlap, no gap

    def test_short_range_single_chunk(self):
        assert chunk_date_range(date(2024, 12, 1), AS_OF, 31) == [(date(2024, 12, 1), AS_OF)]


class TestCanonicalization:
    def test_canonical_url_strips_noise(self):
        assert (
            canonical_url("https://www.Reuters.com/tech/story/?utm_source=x&fbclid=abc&id=9")
            == "reuters.com/tech/story?id=9"
        )
        assert canonical_url("https://reuters.com/tech/story/") == "reuters.com/tech/story"

    def test_normalize_title(self):
        assert normalize_title("  NVIDIA  Unveils: New GPU! ") == "nvidia unveils new gpu"


class TestDeduplicate:
    def test_same_raw_provider_id_clusters(self):
        rep = deduplicate(
            [
                make_article("b", raw_provider_id="same", published_at=dt(2024, 12, 30)),
                make_article("a", raw_provider_id="same", published_at=dt(2024, 12, 29)),
            ]
        )
        assert len(rep) == 1
        assert rep[0].article_id == "a"  # earliest published is the representative
        assert rep[0].source_confirmation == 2
        assert rep[0].cluster_id

    def test_same_canonical_url_with_tracking_params(self):
        rep = deduplicate(
            [
                make_article("x", url="https://reuters.com/story?utm_source=a"),
                make_article("y", url="https://www.reuters.com/story/?fbclid=z"),
            ]
        )
        assert len(rep) == 1
        assert rep[0].source_confirmation == 2

    def test_same_normalized_title_and_date(self):
        rep = deduplicate(
            [
                make_article("p", title="NVIDIA Unveils: New GPU!", published_at=dt(2024, 12, 30, 8)),
                make_article("q", title="nvidia unveils new gpu", published_at=dt(2024, 12, 30, 20)),
            ]
        )
        assert len(rep) == 1
        assert rep[0].source_confirmation == 2

    def test_syndicated_cluster_single_representative(self):
        # no raw_provider_id, no url -> title+date clustering
        members = [
            make_article("s0", title="Same Story", published_at=dt(2024, 12, 30, 8)),
            make_article("s1", title="same story", published_at=dt(2024, 12, 30, 6)),
            make_article("s2", title="SAME STORY", published_at=dt(2024, 12, 30, 20)),
        ]
        rep = deduplicate(members)
        assert len(rep) == 1
        assert rep[0].article_id == "s1"  # earliest published
        assert rep[0].source_confirmation == 3

    def test_distinct_articles_kept_and_sorted(self):
        rep = deduplicate(
            [
                make_article("late", raw_provider_id="r2", published_at=dt(2024, 12, 30)),
                make_article("early", raw_provider_id="r1", published_at=dt(2024, 12, 28)),
            ]
        )
        assert [a.article_id for a in rep] == ["early", "late"]
        assert all(a.source_confirmation == 1 and a.cluster_id == "" for a in rep)


class TestMatchArticle:
    def test_alias_word_boundary(self):
        article = make_article(title="AMDSWER shares surge in trading")
        matched = match_article(article, NEWS_CFG["company_aliases"], TICKER_TO_LABEL)
        assert matched.securities == []  # "AMD" must NOT match "AMDSWER"

    def test_alias_match_and_sector_mapping(self):
        article = make_article(title="NVIDIA Corporation reports record data center revenue")
        matched = match_article(article, NEWS_CFG["company_aliases"], TICKER_TO_LABEL)
        assert matched.securities == ["NVDA"]
        assert matched.sector_tags == ["AI Infrastructure"]

    def test_ticker_symbol_itself_matches(self):
        article = make_article(title="MU guides higher on HBM demand")
        matched = match_article(article, NEWS_CFG["company_aliases"], TICKER_TO_LABEL)
        assert matched.securities == ["MU"]
        assert matched.sector_tags == ["Memory & Storage"]

    def test_nlp_organizations_matched_when_present(self):
        article = make_article(title="Chipmaker beats estimates", organizations=["Micron Technology"])
        matched = match_article(article, NEWS_CFG["company_aliases"], TICKER_TO_LABEL)
        assert matched.securities == ["MU"]

    def test_no_match_empty(self):
        article = make_article(title="Local sports team wins championship")
        matched = match_article(article, NEWS_CFG["company_aliases"], TICKER_TO_LABEL)
        assert matched.securities == [] and matched.sector_tags == []


class TestRankArticles:
    def test_direct_beats_sector_beats_macro(self):
        direct = make_article("direct", securities=["NVDA"], sector_tags=["AI Infrastructure"])
        sector = make_article("sector", sector_tags=["AI Infrastructure"])
        macro = make_article("macro")
        ranked = rank_articles([macro, sector, direct], AS_OF, 10)
        assert [a.article_id for a in ranked] == ["direct", "sector", "macro"]

    def test_recency_bonus(self):
        older = make_article("older", securities=["NVDA"], published_at=dt(2024, 12, 1))
        newer = make_article("newer", securities=["NVDA"], published_at=dt(2024, 12, 30))
        ranked = rank_articles([older, newer], AS_OF, 10)
        assert [a.article_id for a in ranked] == ["newer", "older"]

    def test_sentiment_never_affects_ranking(self):
        negative_direct = make_article("neg", securities=["NVDA"], sentiment=-0.9)
        positive_sector = make_article("pos", sector_tags=["AI Infrastructure"], sentiment=0.9)
        ranked = rank_articles([positive_sector, negative_direct], AS_OF, 10)
        assert [a.article_id for a in ranked] == ["neg", "pos"]  # tier decides, not sentiment

    def test_reputable_domain_bonus(self):
        reputable = make_article("rep", sector_tags=["AI Infrastructure"], source_domain="reuters.com")
        blog = make_article("blog", sector_tags=["AI Infrastructure"], source_domain="random-blog.example")
        ranked = rank_articles([blog, reputable], AS_OF, 10, reputable_domains={"reuters.com"})
        assert [a.article_id for a in ranked] == ["rep", "blog"]

    def test_truncation_to_max(self):
        articles = [make_article(f"a{i}", securities=["NVDA"]) for i in range(5)]
        assert len(rank_articles(articles, AS_OF, 2)) == 2


class TestToNewsRecord:
    def test_provenance_and_usable_from(self):
        article = make_article(
            "xyz",
            url="https://reuters.com/story",
            title="the headline",
            summary="the summary",
            securities=["NVDA"],
            sector_tags=["AI Infrastructure"],
            published_at=dt(2024, 12, 30, 9),
        )
        record = to_news_record(article)
        assert record.news_id == "nc_xyz"
        assert record.source == SourceType.NEWSCATCHER
        assert record.source_ref == "https://reuters.com/story"
        assert record.headline == "the headline"
        assert record.body == "the summary"
        assert record.securities == ["NVDA"]
        assert record.sectors == ["AI Infrastructure"]
        assert record.usable_from == record.published_at == article.published_at
        assert record.retrieved_at == article.retrieved_at


def _plan(query: str = "q", start: date = date(2024, 12, 1), end: date = AS_OF) -> list[NewsQuery]:
    return [
        NewsQuery(
            kind="company",
            query=query,
            label="NVDA",
            sectors=["AI Infrastructure"],
            from_date=start,
            to_date=end,
        )
    ]


class TestGatherNews:
    async def test_pit_future_articles_rejected(self):
        articles = [
            make_article("past", published_at=dt(2024, 12, 31, 15, 45)),  # on as_of: allowed
            make_article("at_cutoff", published_at=CUTOFF),  # exactly at cutoff: allowed
            make_article("future", published_at=dt(2025, 1, 1, 10)),  # after cutoff: rejected
        ]
        provider = MockNewsProvider(articles=articles)
        stats: dict[str, int] = {}
        records = await gather_news(
            provider,
            _plan(),
            AS_OF,
            gate_for(),
            NEWS_CFG,
            aliases=NEWS_CFG["company_aliases"],
            ticker_to_label=TICKER_TO_LABEL,
            stats=stats,
        )
        ids = {r.news_id for r in records}
        assert ids == {"nc_past", "nc_at_cutoff"}
        assert stats["future_dropped"] == 1
        assert stats["returned"] == 2

    async def test_query_assigned_sectors_unioned(self):
        # no content match -> sectors come from the query assignment
        provider = MockNewsProvider(articles=[make_article("plain", title="generic market wrap")])
        records = await gather_news(
            provider,
            _plan(),
            AS_OF,
            gate_for(),
            NEWS_CFG,
            aliases=NEWS_CFG["company_aliases"],
            ticker_to_label=TICKER_TO_LABEL,
        )
        assert records[0].sectors == ["AI Infrastructure"]
        assert records[0].securities == []

    async def test_stats_reported(self):
        provider = MockNewsProvider(articles=[make_article("one"), make_article("one")])
        stats: dict[str, int] = {}
        await gather_news(
            provider,
            _plan(),
            AS_OF,
            gate_for(),
            NEWS_CFG,
            aliases=NEWS_CFG["company_aliases"],
            ticker_to_label=TICKER_TO_LABEL,
            stats=stats,
        )
        assert stats["queries_run"] == 1
        assert stats["articles_raw"] == 2
        assert stats["duplicates_dropped"] == 1  # same article twice -> one cluster
        assert stats["returned"] == 1
        assert stats["api_calls"] == 1

    async def test_max_articles_per_run_enforced(self):
        cfg = {**NEWS_CFG, "provider": {"max_articles_per_run": 2}}
        provider = MockNewsProvider(articles=[make_article(f"m{i}") for i in range(5)])
        stats: dict[str, int] = {}
        records = await gather_news(
            provider,
            _plan(),
            AS_OF,
            gate_for(),
            cfg,
            aliases=cfg["company_aliases"],
            ticker_to_label=TICKER_TO_LABEL,
            stats=stats,
        )
        assert len(records) == 2
        assert stats["returned"] == 2

    async def test_chunking_for_long_windows(self):
        provider = MockNewsProvider(articles=[])
        long_plan = _plan(start=date(2024, 1, 1), end=date(2024, 3, 15))
        stats: dict[str, int] = {}
        await gather_news(
            provider, long_plan, AS_OF, gate_for(), NEWS_CFG, aliases={}, ticker_to_label={}, stats=stats
        )
        assert stats["queries_run"] == 3  # 74 days chunked into 31-day windows


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
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, json: dict[str, Any] | None = None, headers: Any = None) -> Any:
        self.requests.append({"url": url, "json": json})
        return self.script.pop(0)


class TestCachedFutureLeak:
    async def test_cached_future_articles_still_dropped(self, tmp_path):
        """A cache written by a different run must not leak future articles."""
        future_raw = {
            "id": "f1",
            "title": "Future news after the cutoff",
            "published_date": "2025-01-05 09:00:00",
        }
        body = {"status": "ok", "articles": [future_raw]}
        config = {
            "provider": {"backoff_base_seconds": 0.0},
            "cache": {"enabled": True, "dir": str(tmp_path)},
        }
        settings = EnvSettings(newscatcher_api_key="test-key")
        # seed the cache via a search (as a "previous run" would have)
        seeding = NewsCatcherProvider(settings, config=config, client=_FakeClient([_FakeResponse(200, body)]))
        await seeding.search("q", date(2024, 12, 1), AS_OF)

        # this run: cache hit (zero API calls), but the future article is dropped
        provider = NewsCatcherProvider(settings, config=config, client=_FakeClient([]))
        stats: dict[str, int] = {}
        records = await gather_news(
            provider,
            _plan(),
            AS_OF,
            gate_for(),
            NEWS_CFG,
            aliases={},
            ticker_to_label={},
            stats=stats,
        )
        assert records == []
        assert stats["cache_hits"] == 1
        assert stats["api_calls"] == 0
        assert stats["future_dropped"] == 1


class TestDuplicateProvenance:
    def test_dropped_duplicates_carry_duplicate_of(self):
        shared_url = "https://example.com/story?utm_source=feed"
        a1 = make_article("a1", url=shared_url, raw_provider_id="", title="Same story")
        a2 = make_article("a2", url=shared_url + "&utm_medium=x", raw_provider_id="", title="Same story")
        a2 = a2.model_copy(update={"published_at": dt(2024, 12, 30, 13)})
        dropped: list = []
        reps = deduplicate([a1, a2], dropped=dropped)
        assert len(reps) == 1
        assert reps[0].source_confirmation == 2
        assert len(dropped) == 1
        assert dropped[0].duplicate_of == reps[0].article_id

    def test_no_dropped_param_keeps_old_contract(self):
        a1 = make_article("a1", url="https://example.com/x")
        a2 = make_article("a2", url="https://example.com/x")
        assert len(deduplicate([a1, a2])) == 1
