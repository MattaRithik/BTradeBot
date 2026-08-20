"""NewsCatcher provider: async HTTP gateway for NEWS ONLY.

This module NEVER provides prices, returns, fundamentals or bars — market
data is owned by the Bloomberg layer (Desktop API / terminal exports). It
talks to the NewsCatcher v3 search API (``POST {base_url}/api/search`` with
the ``x-api-token`` header), retries honestly (429/5xx/timeouts with
exponential backoff; other 4xx fail immediately), caches raw responses on
disk (deterministic sha256 keys; the ORIGINAL retrieved_at is preserved on
hits, and hits cost zero API calls), and normalizes each article into the
NewsArticle schema. Field-name variants are tolerated; malformed articles
are skipped and counted — never faked, never fatal to the batch. NLP
enrichment is OPTIONAL: normalization never depends on it.

The API key comes from the environment (NEWSCATCHER_API_KEY) only — never
from YAML, never logged, never audited, never serialized.

The httpx.AsyncClient is injectable so contract tests run fully offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import EnvSettings, load_yaml_config
from quant_platform.core.schemas import NewsArticle
from quant_platform.core.timeutil import utc_now

_SEARCH_ENDPOINT = "/api/search"
_RETRYABLE_STATUS = {429}

_DEFAULT_PROVIDER: dict[str, Any] = {
    "timeout_seconds": 30,
    "max_retries": 3,  # total attempts per call
    "backoff_base_seconds": 1.0,
    "page_size": 50,
    "max_pages_per_query": 3,
    "max_api_calls_per_run": 100,
    "max_articles_per_run": 300,
}
_DEFAULT_CACHE: dict[str, Any] = {
    "enabled": True,
    "dir": "data/raw/news_cache",
    # Historical (immutable) windows are cached forever; windows reaching the
    # recent past expire so current-mode runs see fresh news.
    "recent_threshold_days": 2,
    "ttl_hours_recent": 6.0,
}


class NewsCatcherError(RuntimeError):
    """A NewsCatcher call cannot complete honestly. Articles are NEVER faked."""


def load_news_provider_config() -> dict[str, Any]:
    """provider/cache sections of configs/news.yaml merged over safe defaults."""
    try:
        raw = load_yaml_config("news")
    except FileNotFoundError:
        raw = {}
    return {
        "provider": {**_DEFAULT_PROVIDER, **(raw.get("provider") or {})},
        "cache": {**_DEFAULT_CACHE, **(raw.get("cache") or {})},
    }


# -- normalization helpers -----------------------------------------------------
def _squash(text: str) -> str:
    return " ".join(str(text).lower().split())


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                out.append(str(name))
        elif item is not None:
            out.append(str(item))
    return out


def _sentiment(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        pos, neg = value.get("positive"), value.get("negative")
        if isinstance(pos, (int, float)) and isinstance(neg, (int, float)):
            return float(pos) - float(neg)
    return None


def normalize_raw_article(raw: Any, retrieved_at: datetime) -> NewsArticle | None:
    """Normalize one raw provider article; return None when unusable.

    Tolerates field-name variants and missing NLP enrichment. An article is
    unusable when it has no title or an unparseable published date — it is
    skipped and counted by the caller, never faked.
    """
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    published_raw = raw.get("published_date") or raw.get("published_at") or raw.get("pub_date")
    if not published_raw:
        return None
    ts = pd.to_datetime(str(published_raw), utc=True, errors="coerce")
    if pd.isna(ts):
        return None
    published_at = ts.to_pydatetime()

    summary = str(raw.get("summary") or raw.get("description") or raw.get("excerpt") or "")
    content = str(raw.get("content") or raw.get("full_content") or "")
    url = str(raw.get("link") or raw.get("url") or "")
    raw_id = raw.get("id") or raw.get("_id") or raw.get("article_id")
    if raw_id:
        article_id = str(raw_id)
    else:  # deterministic fallback when the provider omits an id
        article_id = hashlib.sha256(f"{title}|{published_at.isoformat()}".encode()).hexdigest()[:16]

    # OPTIONAL NLP enrichment — never required, never depended upon
    nlp = raw.get("nlp") if isinstance(raw.get("nlp"), dict) else {}
    content_hash = hashlib.sha256(f"{_squash(title)}|{_squash(summary)}".encode()).hexdigest()
    return NewsArticle(
        article_id=article_id,
        provider="newscatcher",
        published_at=published_at,
        retrieved_at=retrieved_at,
        title=title,
        summary=summary,
        content=content,
        source_name=str(raw.get("source_name") or raw.get("name_source") or ""),
        source_domain=str(raw.get("domain") or raw.get("source_domain") or ""),
        url=url,
        language=str(raw.get("lang") or raw.get("language") or ""),
        country=str(raw.get("country") or ""),
        organizations=_str_list(nlp.get("organizations", raw.get("organizations"))),
        people=_str_list(nlp.get("people", raw.get("people"))),
        locations=_str_list(nlp.get("locations", raw.get("locations"))),
        sentiment=_sentiment(nlp.get("sentiment", raw.get("sentiment"))),
        raw_provider_id=str(raw_id) if raw_id else "",
        provider_metadata=dict(raw),
        content_hash=content_hash,
    )


class NewsCatcherProvider:
    """NewsCatcher search provider over async httpx. NEWS ONLY by design."""

    name = "newscatcher"

    def __init__(
        self,
        settings: EnvSettings | None = None,
        *,
        config: dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
        audit: AuditLogger | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.settings = settings or EnvSettings.from_env()
        if not self.settings.newscatcher_configured:
            raise NewsCatcherError(
                "NEWSCATCHER_API_KEY is not set — the NewsCatcher gateway cannot "
                "authenticate. Set it in the environment (.env locally) or use "
                "MockNewsProvider for offline runs/tests."
            )
        merged = load_news_provider_config()
        if config:
            merged["provider"].update(config.get("provider") or {})
            merged["cache"].update(config.get("cache") or {})
        p = merged["provider"]
        self.timeout_seconds = float(p["timeout_seconds"])
        self.max_attempts = max(1, int(p["max_retries"]))
        self.backoff_base_seconds = float(p["backoff_base_seconds"])
        self.page_size = int(p["page_size"])
        self.max_pages_per_query = max(1, int(p["max_pages_per_query"]))
        self.max_api_calls_per_run = int(p["max_api_calls_per_run"])
        self.max_articles_per_run = int(p["max_articles_per_run"])
        c = merged["cache"]
        self.cache_enabled = bool(c.get("enabled", True))
        self.cache_dir = (
            Path(cache_dir) if cache_dir is not None else Path(c.get("dir") or _DEFAULT_CACHE["dir"])
        )
        self.cache_recent_threshold_days = int(
            c.get("recent_threshold_days", _DEFAULT_CACHE["recent_threshold_days"])
        )
        self.cache_ttl_hours_recent = float(
            c.get("ttl_hours_recent", _DEFAULT_CACHE["ttl_hours_recent"])
        )
        self.audit = audit
        self._client = client
        self._owns_client = client is None
        self.api_calls = 0  # logical page fetches that hit the network
        self.cache_hits = 0
        self.skipped_articles = 0
        self.articles_fetched = 0  # run-level article budget consumption

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.newscatcher_base_url,
                timeout=self.timeout_seconds,
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- cache ---------------------------------------------------------------
    def _cache_key(self, payload: dict[str, Any]) -> str:
        keyed = {"endpoint": _SEARCH_ENDPOINT, **payload}
        blob = json.dumps(keyed, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> tuple[dict[str, Any], datetime] | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            body = stored["body"]
            retrieved_at = pd.to_datetime(stored["retrieved_at"], utc=True).to_pydatetime()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None  # corrupt cache entry is ignored, never fatal
        return body, retrieved_at

    def _write_cache(self, key: str, body: dict[str, Any], retrieved_at: datetime) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {"retrieved_at": retrieved_at.isoformat(), "body": body}
        self._cache_path(key).write_text(json.dumps(payload, default=str), encoding="utf-8")

    def _cache_stale(self, retrieved_at: datetime, to_date: date) -> bool:
        """Immutable historical windows never expire; recent windows do.

        A query whose ``to`` date is safely in the past asks for a fixed,
        immutable slice of history — cache it forever. A window reaching the
        recent past must refresh after ``ttl_hours_recent`` so current-mode
        runs are not served stale news.
        """
        threshold = utc_now().date() - timedelta(days=self.cache_recent_threshold_days)
        if to_date < threshold:
            return False
        age = utc_now() - retrieved_at
        return age > timedelta(hours=self.cache_ttl_hours_recent)

    # -- the call --------------------------------------------------------------
    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        headers = {"x-api-token": self.settings.newscatcher_api_key}
        last_error = "no attempt made"
        for attempt in range(self.max_attempts):
            try:
                resp = await client.post(_SEARCH_ENDPOINT, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await self._backoff(attempt)
                continue
            if resp.status_code in _RETRYABLE_STATUS or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}"
                await self._backoff(attempt)
                continue
            if resp.status_code >= 400:
                # client error (e.g. 400/401/403): retrying will not help
                raise NewsCatcherError(
                    f"NewsCatcher rejected the request: HTTP {resp.status_code} — {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except json.JSONDecodeError as exc:
                raise NewsCatcherError(f"NewsCatcher returned a non-JSON body: {exc}") from exc
            if not isinstance(body, dict) or not isinstance(body.get("articles"), list):
                raise NewsCatcherError("NewsCatcher response missing the 'articles' list")
            return body
        raise NewsCatcherError(f"NewsCatcher call failed after {self.max_attempts} attempt(s): {last_error}")

    async def _backoff(self, attempt: int) -> None:
        delay = self.backoff_base_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _request_page(
        self,
        query: str,
        from_date: date,
        to_date: date,
        page: int,
        page_size: int | None = None,
    ) -> tuple[dict[str, Any], datetime]:
        """One search page: cache first, then the guarded, retried API call."""
        payload = {
            "q": query,
            "from_": from_date.isoformat(),
            "to_": to_date.isoformat(),
            "page": page,
            "page_size": page_size or self.page_size,
        }
        key = self._cache_key(payload)
        if self.cache_enabled:
            hit = self._read_cache(key)
            if hit is not None and not self._cache_stale(hit[1], to_date):
                self.cache_hits += 1
                return hit
        if self.api_calls >= self.max_api_calls_per_run:
            raise NewsCatcherError(
                f"NewsCatcher max_api_calls_per_run ({self.max_api_calls_per_run}) reached — "
                "refusing further API calls this run"
            )
        body = await self._post_with_retries(payload)
        self.api_calls += 1
        retrieved_at = utc_now()
        if self.cache_enabled:
            self._write_cache(key, body, retrieved_at)
        return body, retrieved_at

    async def search(self, query: str, from_date: date, to_date: date) -> list[NewsArticle]:
        """Search one query over a date range, following pages honestly.

        ``max_articles_per_run`` is a real fetch guard: pagination stops once
        the RUN's article budget is reached (never a silent post-hoc trim).
        """
        articles: list[NewsArticle] = []
        for page in range(1, self.max_pages_per_query + 1):
            if self.articles_fetched + len(articles) >= self.max_articles_per_run:
                break
            body, retrieved_at = await self._request_page(query, from_date, to_date, page)
            raw_articles = body["articles"]
            if not raw_articles:
                break
            for raw in raw_articles:
                article = normalize_raw_article(raw, retrieved_at)
                if article is None:
                    self.skipped_articles += 1  # counted, never faked
                    continue
                articles.append(article)
            if len(raw_articles) < self.page_size:
                break  # short page = last page
        remaining = max(0, self.max_articles_per_run - self.articles_fetched)
        articles = articles[:remaining]
        self.articles_fetched += len(articles)
        return articles

    async def ping(self) -> tuple[str, str]:
        """ONE minimal search (page_size=1, tiny range) for the doctor."""
        try:
            today = utc_now().date()
            body, _ = await self._request_page("markets", today - timedelta(days=1), today, 1, page_size=1)
        except Exception as exc:  # honest diagnostics, never a crash
            return "FAIL", str(exc)
        return "PASS", f"articles={len(body.get('articles', []))}"


class MockNewsProvider:
    """Deterministic offline provider with the same interface. NEWS ONLY.

    ``scripted`` maps a query string to raw dicts (normalized exactly like
    real responses) or NewsArticle objects; ``articles`` is the canned
    response for any unscripted query. Every query string seen is recorded
    in ``queries_seen`` so tests can assert the plan that was executed.
    ``fail_with`` makes every search raise that exception (outage tests).
    """

    name = "newscatcher_mock"

    def __init__(
        self,
        scripted: dict[str, list[dict | NewsArticle]] | None = None,
        articles: list[NewsArticle] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.scripted = dict(scripted or {})
        self.articles = list(articles or [])
        self.fail_with = fail_with
        self.queries_seen: list[str] = []
        self.api_calls = 0
        self.cache_hits = 0
        self.skipped_articles = 0

    async def search(self, query: str, from_date: date, to_date: date) -> list[NewsArticle]:
        if self.fail_with is not None:
            raise self.fail_with
        self.queries_seen.append(query)
        self.api_calls += 1
        items = self.scripted.get(query, self.articles)
        out: list[NewsArticle] = []
        for item in items:
            if isinstance(item, NewsArticle):
                out.append(item)
                continue
            article = normalize_raw_article(item, utc_now())
            if article is None:
                self.skipped_articles += 1
                continue
            out.append(article)
        return out

    async def ping(self) -> tuple[str, str]:
        return "PASS", "mock provider — no network"

    async def aclose(self) -> None:
        return None
