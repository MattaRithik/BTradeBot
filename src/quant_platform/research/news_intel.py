"""News intelligence orchestration: deterministic, NO LLM anywhere.

This module turns configs/news.yaml into a deterministic query plan
(company alias queries, sector queries, macro themes), executes it against
a news provider (NewsCatcher or a mock), matches articles to securities and
sectors with regex word boundaries, deduplicates syndicated copies into
clusters (a repeated story = more confirmation, NOT more independent
evidence), ranks by transparent relevance rules (sentiment is NEVER a
ranking input), and converts the survivors into NewsRecord objects that are
filtered through the TimeGatekeeper — point-in-time enforcement happens in
Python AFTER retrieval, so even a cached response written by a different
run cannot leak future articles into THIS run.

Design decisions documented here:
- one query per sector: multiple configured strings are OR-combined;
- tickers without configured aliases fall back to a plain quoted keyword
  query on the ticker itself (alias lookup is case-insensitive);
- ranking tiers are mutually exclusive: +3 direct security match, +2
  content-matched sector tag, +1 otherwise (query-context only, e.g.
  macro-theme articles with no direct content match);
- query-assigned sectors are unioned into NewsRecord.sectors AFTER ranking,
  so they never inflate an article's relevance tier.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import SourceType
from quant_platform.core.gatekeeper import TimeGatekeeper
from quant_platform.core.schemas import NewsArticle, NewsRecord

_TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "igshid", "ref", "spm"}


def load_news_config() -> dict[str, Any]:
    """The full configs/news.yaml mapping."""
    return load_yaml_config("news")


def load_company_aliases(config: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """ticker (uppercased) -> alias list from the news config."""
    cfg = config if config is not None else load_news_config()
    return {
        str(ticker).upper(): [str(alias) for alias in aliases]
        for ticker, aliases in (cfg.get("company_aliases") or {}).items()
    }


@dataclass(frozen=True)
class NewsQuery:
    """One deterministic news query in the plan."""

    kind: str  # "company" | "sector" | "macro"
    query: str
    label: str  # ticker / sector label / theme id
    sectors: list[str] = field(default_factory=list)  # sector labels results map to
    from_date: date = date.min
    to_date: date = date.max


def build_query_plan(
    tickers: list[str],
    ticker_to_label: dict[str, str],
    as_of: date,
    config: dict[str, Any] | None = None,
) -> list[NewsQuery]:
    """Deterministic query plan: same inputs -> identical plan. No LLM."""
    cfg = config if config is not None else load_news_config()
    windows = cfg.get("windows") or {}
    company_days = int(windows.get("company_days", 30))
    sector_days = int(windows.get("sector_days", 30))
    macro_days = int(windows.get("macro_days", 14))
    aliases = load_company_aliases(cfg)
    label_map = {str(k).upper(): str(v) for k, v in (ticker_to_label or {}).items()}

    plan: list[NewsQuery] = []
    for ticker in tickers:
        t = str(ticker).upper()
        known = aliases.get(t)
        # fallback when no aliases are configured: plain quoted keyword on the ticker
        query = " OR ".join(f'"{a}"' for a in known) if known else f'"{t}"'
        label = label_map.get(t, "")
        plan.append(
            NewsQuery(
                kind="company",
                query=query,
                label=t,
                sectors=[label] if label else [],
                from_date=as_of - timedelta(days=company_days),
                to_date=as_of,
            )
        )

    labels_present = sorted({label_map.get(str(t).upper(), "") for t in tickers} - {""})
    sector_queries = cfg.get("sector_queries") or {}
    for label in labels_present:
        strings = [str(q) for q in (sector_queries.get(label) or [])]
        if not strings:
            continue
        plan.append(
            NewsQuery(
                kind="sector",
                query=" OR ".join(strings),  # one OR-combined query per sector
                label=label,
                sectors=[label],
                from_date=as_of - timedelta(days=sector_days),
                to_date=as_of,
            )
        )

    for theme in cfg.get("macro_themes") or []:
        plan.append(
            NewsQuery(
                kind="macro",
                query=str(theme["query"]),
                label=str(theme["id"]),
                sectors=[str(s) for s in (theme.get("sectors") or [])],
                from_date=as_of - timedelta(days=macro_days),
                to_date=as_of,
            )
        )
    return plan


def chunk_date_range(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """Split [start, end] into contiguous, non-overlapping <=chunk_days windows."""
    if chunk_days < 1:
        raise ValueError(f"chunk_days must be >= 1, got {chunk_days}")
    chunks: list[tuple[date, date]] = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def canonical_url(url: str) -> str:
    """Canonical form for dedup: no scheme, no www., no tracking params,
    no trailing slash, lowercase host."""
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlsplit(u if "://" in u else f"//{u}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    canon = f"{host}{parsed.path.rstrip('/')}"
    if kept:
        canon += "?" + urlencode(sorted(kept))
    return canon


def normalize_title(title: str) -> str:
    """Lowercase, punctuation stripped, whitespace collapsed."""
    return " ".join(re.sub(r"[^\w\s]", " ", (title or "").lower()).split())


def _cluster_key(article: NewsArticle) -> tuple[str, ...]:
    if article.raw_provider_id:
        return ("id", article.raw_provider_id)
    if article.url:
        return ("url", canonical_url(article.url))
    norm = normalize_title(article.title)
    if norm:
        return ("title", norm, article.published_at.date().isoformat())
    return ("hash", article.content_hash)


def deduplicate(
    articles: list[NewsArticle], dropped: list[NewsArticle] | None = None
) -> list[NewsArticle]:
    """Cluster duplicates; return representatives only, sorted by published_at.

    Cluster key precedence: raw_provider_id, else canonical_url, else
    (normalize_title, published date), else content_hash. The representative
    is the earliest-published member; it carries cluster_id and
    source_confirmation = cluster size. Non-representatives are dropped from
    the result; when ``dropped`` is given they are appended there with
    ``duplicate_of`` pointing at the representative's article_id (provenance
    for syndicated-story accounting).
    """
    groups: dict[tuple[str, ...], list[NewsArticle]] = {}
    for article in articles:
        groups.setdefault(_cluster_key(article), []).append(article)
    representatives: list[NewsArticle] = []
    for key, members in groups.items():
        members = sorted(members, key=lambda a: a.published_at)  # stable
        rep = members[0]
        if len(members) > 1:
            cluster_id = "cluster_" + hashlib.sha256(repr(key).encode()).hexdigest()[:12]
            rep = rep.model_copy(update={"cluster_id": cluster_id, "source_confirmation": len(members)})
            if dropped is not None:
                dropped.extend(
                    m.model_copy(update={"duplicate_of": rep.article_id}) for m in members[1:]
                )
        representatives.append(rep)
    representatives.sort(key=lambda a: a.published_at)
    return representatives


def match_article(
    article: NewsArticle,
    aliases: dict[str, list[str]],
    ticker_to_label: dict[str, str],
) -> NewsArticle:
    """Fill securities/sector_tags deterministically from CONTENT ONLY.

    Word-boundary, case-insensitive alias matches over title+summary (plus
    NLP organizations when present). Query-assigned sectors are unioned by
    the caller, never here — this function stays pure on content.
    """
    alias_map = {str(k).upper(): [str(a) for a in v] for k, v in (aliases or {}).items()}
    label_map = {str(k).upper(): str(v) for k, v in (ticker_to_label or {}).items()}
    text = f"{article.title}\n{article.summary}"
    org_text = "\n".join(article.organizations)
    found: set[str] = set()
    for ticker in sorted(set(alias_map) | set(label_map)):
        for term in [ticker, *alias_map.get(ticker, [])]:
            pattern = rf"\b{re.escape(term)}\b"
            if re.search(pattern, text, re.IGNORECASE) or (
                org_text and re.search(pattern, org_text, re.IGNORECASE)
            ):
                found.add(ticker)
                break
    sector_tags = sorted({label_map[t] for t in found if t in label_map})
    return article.model_copy(update={"securities": sorted(found), "sector_tags": sector_tags})


def rank_articles(
    articles: list[NewsArticle],
    as_of: date,
    max_articles: int,
    *,
    reputable_domains: set[str] | None = None,
) -> list[NewsArticle]:
    """Deterministic relevance ranking. Sentiment is NEVER an input.

    Score = tier (+3 direct security match; else +2 content-matched sector
    tag; else +1 query-context only, e.g. macro-theme articles) + recency
    bonus (max 2 points, linear decay to 0 across the oldest age in the
    batch relative to as_of) + 0.5 reputable-domain bonus + duplicate
    confirmation bonus min(1, 0.2 * (source_confirmation - 1)). Sorted by
    (score desc, published_at desc), stable; truncated to max_articles.
    """
    if not articles:
        return []
    window = max(1, max((as_of - a.published_at.date()).days for a in articles))
    reputable = {d.lower() for d in (reputable_domains or set())}
    scored: list[tuple[float, NewsArticle]] = []
    for article in articles:
        if article.securities:
            score = 3.0
        elif article.sector_tags:
            score = 2.0
        else:
            score = 1.0
        age = (as_of - article.published_at.date()).days
        score += 2.0 if age <= 0 else max(0.0, 2.0 * (1.0 - age / window))
        domain = article.source_domain.lower().removeprefix("www.")
        if domain and domain in reputable:
            score += 0.5
        score += min(1.0, 0.2 * max(0, article.source_confirmation - 1))
        scored.append((score, article))
    scored.sort(key=lambda sa: (-sa[0], -sa[1].published_at.timestamp()))
    return [a for _, a in scored[:max_articles]]


def to_news_record(article: NewsArticle) -> NewsRecord:
    """Convert to the NewsRecord the EvidenceEngine consumes.

    usable_from == published_at: an article is usable the instant it was
    published; the gatekeeper still re-checks it against the run cutoff.
    """
    return NewsRecord(
        news_id=f"nc_{article.article_id}",
        source=SourceType.NEWSCATCHER,
        source_ref=article.url or article.article_id,
        headline=article.title,
        body=article.summary or article.content,
        securities=list(article.securities),
        sectors=list(article.sector_tags),
        event_time=article.published_at,
        published_at=article.published_at,
        usable_from=article.published_at,
        retrieved_at=article.retrieved_at,
    )


def dedupe_news_records(records: list[NewsRecord]) -> list[NewsRecord]:
    """Deterministic cross-source dedup of NewsRecords.

    The same normalized-headline + published-DATE story arriving from both
    NewsCatcher and a Bloomberg export is ONE story: keep the NewsCatcher
    record (richer provenance); otherwise the first record wins. The key
    uses the published date (not the instant) because Bloomberg export news
    is day-granular. Returns survivors sorted by published_at.
    """
    best: dict[tuple[str, str], NewsRecord] = {}
    for record in records:
        key = (normalize_title(record.headline), record.published_at.date().isoformat())
        existing = best.get(key)
        if existing is None or (
            existing.source != SourceType.NEWSCATCHER and record.source == SourceType.NEWSCATCHER
        ):
            best[key] = record
    return sorted(best.values(), key=lambda r: r.published_at)


async def gather_news(
    provider: Any,
    plan: list[NewsQuery],
    as_of_date: date,
    gate: TimeGatekeeper,
    config: dict[str, Any] | None = None,
    aliases: dict[str, list[str]] | None = None,
    ticker_to_label: dict[str, str] | None = None,
    stats: dict[str, int] | None = None,
) -> list[NewsRecord]:
    """Execute the plan and return PIT-safe NewsRecords.

    Windows longer than chunk_days are chunked (never one giant request).
    Articles are content-matched, deduplicated, ranked and truncated to
    max_articles_per_run; query-assigned sectors are unioned in at record
    conversion. Every record then passes gate.filter_by_usable_from — after
    retrieval AND after any cache read — plus a defensive published_at
    cutoff check, so no future article can enter this run.
    """
    cfg = config if config is not None else load_news_config()
    windows = cfg.get("windows") or {}
    chunk_days = int(windows.get("chunk_days", 31))
    provider_cfg = cfg.get("provider") or {}
    max_articles = int(provider_cfg.get("max_articles_per_run", 300))
    reputable = {str(d) for d in (cfg.get("reputable_domains") or [])}
    aliases = load_company_aliases(cfg) if aliases is None else aliases
    ticker_to_label = {} if ticker_to_label is None else ticker_to_label

    local = {
        "queries_run": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "articles_raw": 0,
        "duplicates_dropped": 0,
        "future_dropped": 0,
        "returned": 0,
    }
    api_before = getattr(provider, "api_calls", 0)
    cache_before = getattr(provider, "cache_hits", 0)

    collected: dict[str, NewsArticle] = {}
    query_sectors: dict[str, set[str]] = {}
    matched_count = 0
    for news_query in plan:
        for start, end in chunk_date_range(news_query.from_date, news_query.to_date, chunk_days):
            found = await provider.search(news_query.query, start, end)
            local["queries_run"] += 1
            local["articles_raw"] += len(found)
            for article in found:
                matched = match_article(article, aliases, ticker_to_label)
                matched_count += 1
                if matched.article_id not in collected:
                    collected[matched.article_id] = matched
                    query_sectors[matched.article_id] = set(news_query.sectors)
                else:
                    query_sectors[matched.article_id] |= set(news_query.sectors)

    articles = list(collected.values())
    deduped = deduplicate(articles)
    local["duplicates_dropped"] = matched_count - len(deduped)
    ranked = rank_articles(deduped, as_of_date, max_articles, reputable_domains=reputable)

    records = [
        to_news_record(
            a.model_copy(
                update={"sector_tags": sorted(set(a.sector_tags) | query_sectors.get(a.article_id, set()))}
            )
        )
        for a in ranked
    ]

    # PIT enforcement AFTER retrieval (and after any cache read): future
    # articles can never enter this run, whatever the provider handed back.
    kept = gate.filter_by_usable_from(records, what="newscatcher_news")
    cutoff = gate.context.cutoff_instant
    kept = [r for r in kept if r.published_at <= cutoff]  # defensive second wall

    local["future_dropped"] = len(records) - len(kept)
    local["returned"] = len(kept)
    local["api_calls"] = getattr(provider, "api_calls", 0) - api_before
    local["cache_hits"] = getattr(provider, "cache_hits", 0) - cache_before
    if stats is not None:
        stats.update(local)
    return kept
