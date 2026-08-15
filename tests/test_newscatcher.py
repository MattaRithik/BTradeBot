"""NewsCatcherProvider contract tests: fully offline via an injected fake client.

Mirrors the Kimi contract-test pattern: the real network is never touched;
the provider must authenticate via x-api-token (never logged/serialized),
retry honestly, paginate, cache deterministically with the original
retrieved_at, guard the per-run API-call budget, and fail honestly on
malformed responses. Individual malformed articles are skipped, never faked.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import httpx
import pytest

from quant_platform.core.config import EnvSettings
from quant_platform.core.schemas import NewsArticle
from quant_platform.data.newscatcher import (
    MockNewsProvider,
    NewsCatcherError,
    NewsCatcherProvider,
)

_SECRET = "nc-test-secret-key"
_FROM = date(2024, 12, 1)
_TO = date(2024, 12, 31)


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if body is not None else "<not json>"

    def json(self) -> dict[str, Any]:
        if self._body is None:
            raise json.JSONDecodeError("no json", self.text, 0)
        return self._body


class FakeClient:
    """Plays back a script of FakeResponse / Exception items, records requests."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def post(
        self, url: str, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> FakeResponse:
        self.requests.append({"url": url, "json": json, "headers": headers})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _settings(**overrides: Any) -> EnvSettings:
    return EnvSettings(newscatcher_api_key=_SECRET, **overrides)


def _config(**provider_overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "provider": {"backoff_base_seconds": 0.0, "page_size": 50},
        "cache": {"enabled": False},
    }
    cfg["provider"].update(provider_overrides)
    return cfg


def _cache_config(cache_dir: Any, **provider_overrides: Any) -> dict[str, Any]:
    cfg = _config(**provider_overrides)
    cfg["cache"] = {"enabled": True, "dir": str(cache_dir)}
    return cfg


def _raw_article(article_id: str = "art1", **overrides: Any) -> dict[str, Any]:
    raw = {
        "id": article_id,
        "title": "NVIDIA unveils new data center GPU",
        "summary": "NVIDIA Corporation announced a new accelerator.",
        "content": "Full story body.",
        "link": "https://reuters.com/technology/nvidia-gpu",
        "published_date": "2024-12-15 10:30:00",
        "source_name": "Reuters",
        "domain": "reuters.com",
        "lang": "en",
        "country": "US",
        "nlp": {
            "organizations": ["NVIDIA Corporation"],
            "people": ["Jensen Huang"],
            "locations": ["Santa Clara"],
            "sentiment": 0.42,
        },
    }
    raw.update(overrides)
    return raw


def _body(*articles: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "total_hits": len(articles), "articles": list(articles)}


def _provider(client: FakeClient, **kwargs: Any) -> NewsCatcherProvider:
    kwargs.setdefault("settings", _settings())
    kwargs.setdefault("config", _config())
    kwargs.setdefault("client", client)
    return NewsCatcherProvider(**kwargs)


class TestNormalization:
    async def test_full_article_normalized(self):
        client = FakeClient([FakeResponse(200, _body(_raw_article()))])
        provider = _provider(client)
        (article,) = await provider.search("nvidia", _FROM, _TO)
        assert article.article_id == "art1"
        assert article.provider == "newscatcher"
        assert article.raw_provider_id == "art1"
        assert article.title == "NVIDIA unveils new data center GPU"
        assert article.summary.startswith("NVIDIA Corporation")
        assert article.content == "Full story body."
        assert article.url == "https://reuters.com/technology/nvidia-gpu"
        assert article.published_at.year == 2024 and article.published_at.tzinfo is not None
        assert article.source_name == "Reuters"
        assert article.source_domain == "reuters.com"
        assert article.language == "en"
        assert article.country == "US"
        assert article.organizations == ["NVIDIA Corporation"]
        assert article.people == ["Jensen Huang"]
        assert article.locations == ["Santa Clara"]
        assert article.sentiment == pytest.approx(0.42)
        assert article.content_hash  # non-empty
        assert article.provider_metadata["id"] == "art1"
        assert article.source_confirmation == 1

    async def test_missing_nlp_still_works(self):
        raw = _raw_article()
        del raw["nlp"]
        client = FakeClient([FakeResponse(200, _body(raw))])
        provider = _provider(client)
        (article,) = await provider.search("nvidia", _FROM, _TO)
        assert article.sentiment is None
        assert article.organizations == [] and article.people == [] and article.locations == []

    async def test_field_variants_tolerated(self):
        raw = {
            "_id": "var1",
            "title": "Variant names article",
            "description": "a description",
            "full_content": "full content",
            "url": "https://ft.com/content/123",
            "published_at": "2024-12-20T08:00:00Z",
            "name_source": "FT",
            "source_domain": "ft.com",
            "language": "en",
        }
        client = FakeClient([FakeResponse(200, _body(raw))])
        provider = _provider(client)
        (article,) = await provider.search("q", _FROM, _TO)
        assert article.article_id == "var1"
        assert article.raw_provider_id == "var1"
        assert article.summary == "a description"
        assert article.content == "full content"
        assert article.url == "https://ft.com/content/123"
        assert article.source_name == "FT"
        assert article.source_domain == "ft.com"
        assert article.language == "en"
        assert article.sentiment is None

    async def test_unparseable_date_skipped(self):
        client = FakeClient([FakeResponse(200, _body(_raw_article(published_date="not a date")))])
        provider = _provider(client)
        assert await provider.search("q", _FROM, _TO) == []
        assert provider.skipped_articles == 1

    async def test_missing_title_skipped(self):
        client = FakeClient([FakeResponse(200, _body(_raw_article(title="")))])
        provider = _provider(client)
        assert await provider.search("q", _FROM, _TO) == []
        assert provider.skipped_articles == 1


class TestAuth:
    async def test_api_token_header_sent(self):
        client = FakeClient([FakeResponse(200, _body(_raw_article()))])
        provider = _provider(client)
        await provider.search("q", _FROM, _TO)
        assert client.requests[0]["url"] == "/api/search"
        assert client.requests[0]["headers"] == {"x-api-token": _SECRET}

    async def test_payload_shape(self):
        client = FakeClient([FakeResponse(200, _body())])
        provider = _provider(client)
        await provider.search("nvidia news", _FROM, _TO)
        body = client.requests[0]["json"]
        assert body["q"] == "nvidia news"
        assert body["from_"] == "2024-12-01"
        assert body["to"] == "2024-12-31"
        assert body["page"] == 1
        assert body["page_size"] == 50

    def test_key_never_serialized(self):
        settings = _settings()
        assert _SECRET not in json.dumps(settings.model_dump(), default=str)
        assert _SECRET not in settings.model_dump_json()

    def test_missing_api_key_refused_at_construction(self):
        with pytest.raises(NewsCatcherError, match="NEWSCATCHER_API_KEY"):
            NewsCatcherProvider(settings=EnvSettings(newscatcher_api_key=""), config=_config())


class TestPagination:
    async def test_two_pages_then_stop(self):
        client = FakeClient(
            [
                FakeResponse(200, _body(_raw_article("a1"), _raw_article("a2"))),
                FakeResponse(200, _body(_raw_article("a3"))),
            ]
        )
        provider = _provider(client, config=_config(page_size=2))
        articles = await provider.search("q", _FROM, _TO)
        assert [a.article_id for a in articles] == ["a1", "a2", "a3"]
        assert [r["json"]["page"] for r in client.requests] == [1, 2]

    async def test_max_pages_cap_respected(self):
        client = FakeClient(
            [
                FakeResponse(200, _body(_raw_article("a1"), _raw_article("a2"))),
                FakeResponse(200, _body(_raw_article("a3"), _raw_article("a4"))),
            ]
        )
        provider = _provider(client, config=_config(page_size=2, max_pages_per_query=1))
        articles = await provider.search("q", _FROM, _TO)
        assert [a.article_id for a in articles] == ["a1", "a2"]
        assert len(client.requests) == 1

    async def test_empty_first_page_stops(self):
        client = FakeClient([FakeResponse(200, _body())])
        provider = _provider(client)
        assert await provider.search("q", _FROM, _TO) == []
        assert len(client.requests) == 1


class TestRetries:
    async def test_retry_then_success_on_429(self):
        client = FakeClient(
            [
                FakeResponse(429, {"error": "rate limited"}),
                FakeResponse(200, _body(_raw_article())),
            ]
        )
        provider = _provider(client)
        (article,) = await provider.search("q", _FROM, _TO)
        assert article.article_id == "art1"
        assert len(client.requests) == 2

    async def test_retry_then_success_on_timeout(self):
        client = FakeClient(
            [
                httpx.TimeoutException("slow"),
                FakeResponse(200, _body(_raw_article())),
            ]
        )
        provider = _provider(client)
        (article,) = await provider.search("q", _FROM, _TO)
        assert article.article_id == "art1"
        assert len(client.requests) == 2

    async def test_500s_exhausted_raise_honestly(self):
        client = FakeClient([FakeResponse(500, {"error": "boom"})] * 3)
        provider = _provider(client)
        with pytest.raises(NewsCatcherError, match="after 3 attempt"):
            await provider.search("q", _FROM, _TO)
        assert len(client.requests) == 3

    async def test_401_not_retried(self):
        client = FakeClient([FakeResponse(401, {"error": "bad key"})])
        provider = _provider(client)
        with pytest.raises(NewsCatcherError, match="HTTP 401"):
            await provider.search("q", _FROM, _TO)
        assert len(client.requests) == 1  # retrying a 4xx is pointless


class TestCache:
    async def test_second_identical_search_hits_cache(self, tmp_path):
        client = FakeClient([FakeResponse(200, _body(_raw_article()))])
        provider = _provider(client, config=_cache_config(tmp_path))
        first = await provider.search("q", _FROM, _TO)
        second = await provider.search("q", _FROM, _TO)
        assert len(client.requests) == 1  # no second HTTP call
        assert provider.api_calls == 1
        assert provider.cache_hits == 1
        assert [a.article_id for a in second] == [a.article_id for a in first]
        # the ORIGINAL retrieved_at is preserved on a cache hit
        assert second[0].retrieved_at == first[0].retrieved_at

    async def test_cache_filename_deterministic(self, tmp_path):
        client1 = FakeClient([FakeResponse(200, _body(_raw_article()))])
        provider1 = _provider(client1, config=_cache_config(tmp_path))
        await provider1.search("q", _FROM, _TO)
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert re.fullmatch(r"[0-9a-f]{64}\.json", files[0].name)
        # a second provider with identical params produces the same file, no new files
        client2 = FakeClient([FakeResponse(200, _body(_raw_article()))])
        provider2 = _provider(client2, config=_cache_config(tmp_path))
        await provider2.search("q", _FROM, _TO)
        assert len(client2.requests) == 0  # cache hit
        assert list(tmp_path.iterdir()) == files


class TestGuards:
    async def test_max_api_calls_per_run_guard(self, tmp_path):
        client = FakeClient([FakeResponse(200, _body(_raw_article("a1")))])
        provider = _provider(
            client, config=_config(page_size=1, max_pages_per_query=3, max_api_calls_per_run=1)
        )
        with pytest.raises(NewsCatcherError, match="max_api_calls_per_run"):
            await provider.search("q", _FROM, _TO)
        assert provider.api_calls == 1
        assert len(client.requests) == 1  # refused BEFORE the second network call

    async def test_non_json_body_raises(self):
        client = FakeClient([FakeResponse(200, None)])
        provider = _provider(client)
        with pytest.raises(NewsCatcherError, match="non-JSON"):
            await provider.search("q", _FROM, _TO)

    async def test_missing_articles_key_raises(self):
        client = FakeClient([FakeResponse(200, {"unexpected": True})])
        provider = _provider(client)
        with pytest.raises(NewsCatcherError, match="articles"):
            await provider.search("q", _FROM, _TO)


class TestPing:
    async def test_ping_success(self):
        client = FakeClient([FakeResponse(200, _body(_raw_article()))])
        provider = _provider(client)
        status, detail = await provider.ping()
        assert status == "PASS"
        assert "articles=1" in detail
        assert client.requests[0]["json"]["page_size"] == 1

    async def test_ping_failure_honest(self):
        client = FakeClient([FakeResponse(401, {"error": "bad key"})])
        provider = _provider(client)
        status, detail = await provider.ping()
        assert status == "FAIL"
        assert "HTTP 401" in detail
        assert _SECRET not in detail  # failures never leak the key


class TestMockProvider:
    async def test_scripted_by_query_and_queries_seen(self):
        mock = MockNewsProvider(scripted={"q1": [_raw_article("m1")]})
        first = await mock.search("q1", _FROM, _TO)
        second = await mock.search("q2", _FROM, _TO)
        assert [a.article_id for a in first] == ["m1"]
        assert second == []
        assert mock.queries_seen == ["q1", "q2"]

    async def test_scripted_raw_dicts_normalized_like_real(self):
        mock = MockNewsProvider(scripted={"q": [_raw_article("m2")]})
        (article,) = await mock.search("q", _FROM, _TO)
        assert isinstance(article, NewsArticle)
        assert article.provider == "newscatcher"
        assert article.raw_provider_id == "m2"

    async def test_canned_articles_for_any_query(self):
        canned = [
            NewsArticle(
                article_id="c1",
                provider="newscatcher",
                published_at="2024-12-15T10:00:00Z",
                retrieved_at="2024-12-15T10:00:00Z",
                title="canned",
                content_hash="abc",
            )
        ]
        mock = MockNewsProvider(articles=canned)
        assert await mock.search("anything", _FROM, _TO) == canned

    async def test_mock_ping(self):
        status, _ = await MockNewsProvider().ping()
        assert status == "PASS"


class TestLive:
    @pytest.mark.skipif(
        __import__("os").environ.get("NEWSCATCHER_LIVE_TEST") != "1",
        reason="opt-in live API test",
    )
    async def test_live_ping(self):
        settings = EnvSettings.from_env()
        if not settings.newscatcher_configured:
            pytest.skip("NEWSCATCHER_API_KEY not set")
        provider = NewsCatcherProvider(settings)
        try:
            status, detail = await provider.ping()
        finally:
            await provider.aclose()
        assert status == "PASS", detail
