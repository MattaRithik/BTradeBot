"""REAL research runtime: Bloomberg data + Kimi reasoning, demo invariants kept.

Mirrors ``pipeline.run_demo`` stage-for-stage, but the market data comes from
the real Bloomberg layer (Desktop API when blpapi is importable, terminal
CSV/XLSX exports otherwise) and the reasoning from the real Kimi gateway. The
offline demo (``quantctl demo``) is untouched; this module reuses the same
components so the same invariants hold:

- all research data flows through the PITRepository / TimeGatekeeper —
  nothing usable after the as-of cutoff can enter the run;
- future returns never enter research: the backtest opens the test window
  via FutureDataGate only AFTER the prediction snapshot is frozen;
- Kimi does language reasoning only; Python does every calculation;
- the LLM never touches broker/execution: this module refuses to run unless
  TRADING_MODE=paper and DRY_RUN=true.

News has two sources, combined per run: the NewsCatcher API is the primary
automated feed (NEWS ONLY — it never provides market data), and manually
exported Bloomberg terminal news remains a first-class additional source.
Both are normalized to NewsRecord (provenance via NewsRecord.source),
deduplicated across sources, and filtered through the TimeGatekeeper AFTER
retrieval, so even a cached NewsCatcher response cannot leak future
articles into a run. If NewsCatcher fails, configs/news.yaml
``on_primary_failure`` decides: "degrade" continues loudly on export news
only; "fail" aborts. Evidence is NEVER fabricated.

Failures are honest: no market data, no news, or an unreachable
provider raises ResearchRuntimeError — output is never faked.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from quant_platform.agents.orchestrator import AgentOrchestrator
from quant_platform.analysis import build_failure_record
from quant_platform.backtest import BacktestConfig, run_backtest
from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import EnvSettings, load_all_configs, load_yaml_config
from quant_platform.core.enums import AuditEventType, SourceType
from quant_platform.core.gatekeeper import FutureDataGate, ResearchContext, TimeGatekeeper
from quant_platform.core.schemas import (
    EvidenceCard,
    EvidencePackage,
    NewsRecord,
    SectorSubmission,
    WalkForwardSplit,
)
from quant_platform.core.store import ArtifactStore
from quant_platform.core.timeutil import start_of_day_utc, utc_now
from quant_platform.data.barstore import BarStore, CachingMarketProvider
from quant_platform.data.bloomberg_desktop import BloombergDesktopAdapter
from quant_platform.data.bloomberg_export import BloombergExportAdapter
from quant_platform.data.newscatcher import NewsCatcherError, NewsCatcherProvider
from quant_platform.data.repository import PITRepository
from quant_platform.data.validation import DataValidationError
from quant_platform.features.engine import compute_features
from quant_platform.models import KimiProvider, ModelProvider, ModelRequest
from quant_platform.pipeline import _sector_label_map
from quant_platform.portfolio import apply_risk_constraints, build_strategy
from quant_platform.research import (
    EvidenceEngine,
    build_thesis,
    check_tradability,
    compute_score,
    group_evidence_by_sector,
    load_scoring_config,
    map_sector_etfs,
    map_sector_securities,
    rank_sectors,
    validate_thesis,
)
from quant_platform.research.news_intel import (
    build_query_plan,
    dedupe_news_records,
    gather_news,
    load_news_config,
)
from quant_platform.signals import build_signals
from quant_platform.snapshots import freeze_snapshot

_NEWS_SUFFIXES = {".csv", ".xlsx", ".xls"}
_SECURITY_COLS = ("security", "ticker")
_DATE_COLS = ("date", "published", "published_at")
_HEADLINE_COLS = ("headline", "title")
_BODY_COLS = ("body", "story", "text")
_EVIDENCE_BATCH = 16  # keep prompts small for the real API


class ResearchRuntimeError(RuntimeError):
    """A real research run cannot proceed honestly. Never fake output."""


def _pick_column(lower: dict[str, str], names: tuple[str, ...]) -> str | None:
    return next((lower[n] for n in names if n in lower), None)


def load_exported_news(
    news_dir: Path | str,
    ticker_to_label: dict[str, str],
    *,
    stats: dict[str, int] | None = None,
) -> list[NewsRecord]:
    """Load Bloomberg-exported news CSV/XLSX files directly under ``news_dir``.

    Column-name variants are tolerated (security/ticker, date/published,
    headline/title, body/story/text). Malformed rows are skipped and counted
    in ``stats`` (never silently). A news item dated D becomes visible at
    start of day D UTC — the same conservative convention as the demo.
    Returns [] (never raises) when the directory is missing or empty.
    """
    news_dir = Path(news_dir)
    if not news_dir.is_dir():
        return []
    records: list[NewsRecord] = []
    files_read = rows_skipped = 0
    for path in sorted(p for p in news_dir.iterdir() if p.suffix.lower() in _NEWS_SUFFIXES):
        try:
            df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
        except Exception:
            rows_skipped += 1  # unreadable file — counted, not hidden
            continue
        df.columns = [str(c).strip() for c in df.columns]
        lower = {c.lower(): c for c in df.columns}
        sec_col = _pick_column(lower, _SECURITY_COLS)
        date_col = _pick_column(lower, _DATE_COLS)
        head_col = _pick_column(lower, _HEADLINE_COLS)
        if sec_col is None or date_col is None or head_col is None:
            rows_skipped += len(df)
            continue
        body_col = _pick_column(lower, _BODY_COLS)
        files_read += 1
        for i, row in enumerate(df.itertuples()):
            raw_sec, raw_date, raw_head = (
                getattr(row, sec_col),
                getattr(row, date_col),
                getattr(row, head_col),
            )
            ts = pd.to_datetime(str(raw_date), errors="coerce")
            if pd.isna(ts) or pd.isna(raw_sec) or pd.isna(raw_head):
                rows_skipped += 1
                continue
            ticker = str(raw_sec).replace(" US Equity", "").strip()
            headline = str(raw_head).strip()
            if not ticker or not headline or headline.lower() == "nan":
                rows_skipped += 1
                continue
            published = start_of_day_utc(ts.date())
            body = getattr(row, body_col) if body_col is not None else ""
            records.append(
                NewsRecord(
                    news_id=f"bnews_{ticker}_{ts.date().isoformat()}_{i}",
                    source=SourceType.BLOOMBERG_EXPORT,
                    source_ref=str(path),
                    headline=headline,
                    body="" if body is None or (isinstance(body, float) and pd.isna(body)) else str(body),
                    securities=[ticker],
                    sectors=[ticker_to_label[ticker]] if ticker in ticker_to_label else [],
                    published_at=published,
                    usable_from=published,
                    retrieved_at=utc_now(),
                )
            )
    if stats is not None:
        stats["files_read"] = files_read
        stats["rows_skipped"] = rows_skipped
    return records


class _DesktopSecuritiesFacade:
    """Prefixes plain tickers (``NVDA`` -> ``NVDA US Equity``) so the
    PITRepository can always pass plain tickers to the Desktop API adapter."""

    name = "bloomberg_desktop"

    def __init__(self, adapter: BloombergDesktopAdapter) -> None:
        self._adapter = adapter

    def get_history(
        self, tickers: list[str], start: date, end: date, fields: list[str] | None = None
    ) -> list:
        securities = [t if " " in t else f"{t} US Equity" for t in tickers]
        return self._adapter.get_history(securities, start, end)


async def kimi_doctor_ping(settings: EnvSettings, *, client: Any = None) -> tuple[str, str]:
    """ONE minimal real Kimi call for the doctor. Returns (status, detail)."""
    gateway = {
        "max_retries": 1,
        "cache_enabled": False,
        "timeout_seconds": 30,
        "backoff_base_seconds": 0.0,
        "pricing_usd_per_mtok": {},
    }
    try:
        provider = KimiProvider(settings, gateway=gateway, client=client, run_id="doctor_ping")
        try:
            response = await provider.complete(
                ModelRequest(
                    task="doctor_ping",
                    system_prompt="You are a connectivity health check. Answer briefly.",
                    user_prompt="Reply with the single word: OK",
                    max_tokens=8,
                    temperature=0.0,
                )
            )
        finally:
            await provider.aclose()
    except Exception as exc:  # honest diagnostics, never a crash
        return "FAIL", str(exc)
    tokens = response.prompt_tokens + response.completion_tokens
    return "PASS", f"model={response.model} tokens={tokens}"


async def newscatcher_doctor_ping(settings: EnvSettings, *, client: Any = None) -> tuple[str, str]:
    """ONE minimal real NewsCatcher call for the doctor. Returns (status, detail)."""
    try:
        provider = NewsCatcherProvider(settings, client=client)
        try:
            status, detail = await provider.ping()
        finally:
            await provider.aclose()
    except Exception as exc:  # honest diagnostics, never a crash
        return "FAIL", str(exc)
    return status, detail


async def run_research(
    data_root: Path | str,
    settings: EnvSettings,
    *,
    as_of: date | None = None,
    history_days: int = 400,
    tickers: list[str] | None = None,
    market_adapter: Any = None,
    provider: ModelProvider | None = None,
    news_dir: Path | None = None,
    news_provider: Any = None,
    news_config: dict[str, Any] | None = None,
    audit: AuditLogger | None = None,
    with_backtest: bool = True,
) -> dict[str, Any]:
    """Run the full research pipeline on REAL data. Returns a summary dict."""
    # safety gate FIRST — the research runtime never enables live trading
    if settings.trading_mode != "paper" or not settings.dry_run:
        raise ResearchRuntimeError("TRADING_MODE must be 'paper' and DRY_RUN must be true — refusing to run")

    data_root = Path(data_root)
    inbox = Path(load_yaml_config("bloomberg")["export"]["inbox"])
    tickers = tickers or list(load_yaml_config("universe")["college_test_universe"])
    ticker_to_label, sector_labels = _sector_label_map()

    # 1. real market data source: cache-first bar store backed by the desktop
    # API when blpapi is importable, the terminal export inbox otherwise.
    # The store never decides visibility — PITRepository/TimeGatekeeper do.
    if market_adapter is None:
        desktop = BloombergDesktopAdapter.from_config(settings)
        if desktop.package_available:
            inner: Any = _DesktopSecuritiesFacade(desktop)
            source_name = "bloomberg_desktop"
        else:
            inner = BloombergExportAdapter(inbox)
            source_name = "bloomberg_export"
        bar_store = BarStore(data_root / "cache" / "bloomberg")
        source: Any = CachingMarketProvider(bar_store, inner=inner)
    else:
        source = market_adapter
        source_name = getattr(market_adapter, "name", type(market_adapter).__name__)

    end = date.today()
    start = end - timedelta(days=history_days)
    try:
        full_bars = source.get_history(tickers, start, end)
    except Exception as exc:
        raise ResearchRuntimeError(f"market data fetch via {source_name} failed honestly: {exc}") from exc
    if not full_bars:
        raise ResearchRuntimeError(
            f"Bloomberg unavailable or returned no data via {source_name} for "
            f"{tickers} ({start}..{end}) — run `quantctl bloomberg doctor` or drop "
            f"terminal exports into {inbox}"
        )

    # 2. research context: as_of ~63 trading days before the last bar unless given
    all_days = sorted({b.timestamp.date() for b in full_bars})
    if as_of is None:
        as_of = all_days[max(0, len(all_days) - 64)]
    post_days = [d for d in all_days if d > as_of]
    warnings: list[str] = []
    has_test_data = bool(post_days)
    if has_test_data:
        test_start: date | None = post_days[0]
        test_end: date | None = post_days[-1]
    else:
        # research decisions freeze WITHOUT future endpoints — evaluation
        # happens later via `quantctl evaluate snapshot` once data exists
        test_start, test_end = None, None
        warnings.append("no post-as-of data yet — snapshot frozen, evaluate later (backtest skipped)")

    run_id = f"research_{as_of.isoformat()}_{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    context = ResearchContext(
        run_id=run_id,
        as_of_date=as_of,
        visible_start=start,
        visible_end=as_of,
        test_start=test_start,
        test_end=test_end,
    )

    # 3. gatekeeper-filtered data access — the ONLY data path into research
    store = ArtifactStore(data_root)
    repo = PITRepository(source, store=store, audit=audit)
    bars = repo.get_bars(context, tickers, start, as_of)
    df = pd.DataFrame([b.model_dump() for b in bars])
    features = compute_features(df, as_of, benchmark="SPY" if "SPY" in tickers else tickers[0])
    store.save_table("features", f"features_{run_id}", features)

    news_dir = Path(news_dir) if news_dir is not None else inbox / "news"
    news_config = news_config if news_config is not None else load_news_config()
    gate = TimeGatekeeper(context, audit=audit)

    # 3a. Bloomberg export news — first-class additional source (unchanged)
    news_stats: dict[str, int] = {}
    export_news = load_exported_news(news_dir, ticker_to_label, stats=news_stats)
    if news_stats.get("rows_skipped"):
        warnings.append(f"{news_stats['rows_skipped']} malformed news row(s)/file(s) skipped in {news_dir}")

    # 3b. NewsCatcher — primary automated feed (NEWS ONLY; never market data)
    nc_stats: dict[str, int] = {}
    nc_records: list[NewsRecord] = []
    newscatcher_active = news_provider is not None or settings.newscatcher_configured
    if newscatcher_active:
        catcher = news_provider
        if catcher is None:
            cache_dir = Path((news_config.get("cache") or {}).get("dir", "data/raw/news_cache"))
            catcher = NewsCatcherProvider(settings, audit=audit, cache_dir=cache_dir)
        plan = build_query_plan(tickers, ticker_to_label, as_of, news_config)
        try:
            nc_records = await gather_news(
                catcher,
                plan,
                as_of,
                gate,
                config=news_config,
                ticker_to_label=ticker_to_label,
                stats=nc_stats,
            )
        except NewsCatcherError as exc:
            if audit is not None:
                audit.record(
                    AuditEventType.DATA_QUALITY_ISSUE,
                    run_id=run_id,
                    as_of_date=as_of.isoformat(),
                    what="newscatcher_gather",
                    error=str(exc)[:300],
                )
            warnings.append(f"NewsCatcher FAILED: {exc} — news evidence incomplete")
            if str(news_config.get("on_primary_failure", "degrade")).lower() == "fail":
                raise ResearchRuntimeError(f"NewsCatcher failed and on_primary_failure=fail: {exc}") from exc
            # "degrade": continue loudly with Bloomberg export news only
        finally:
            if news_provider is None:  # we own the constructed provider
                await catcher.aclose()
    else:
        warnings.append("NewsCatcher not configured (NEWSCATCHER_API_KEY unset) — Bloomberg export news only")

    # 3c. combine + cross-source dedup, then ONE gatekeeper pass over everything
    combined_news = dedupe_news_records([*nc_records, *export_news])
    news = gate.filter_by_usable_from(combined_news, what="news_record")
    if not news:
        raise ResearchRuntimeError(
            f"no Bloomberg news export found at {news_dir} and no NewsCatcher news gathered — "
            "set NEWSCATCHER_API_KEY (news API) and/or export news CSV/XLSX from the terminal "
            "into that folder; machine-readable Bloomberg news API is NOT_ENTITLED on this machine"
        )
    news_sources = {
        "newscatcher": sum(1 for n in news if n.source == SourceType.NEWSCATCHER),
        "bloomberg_export": sum(1 for n in news if n.source == SourceType.BLOOMBERG_EXPORT),
    }

    # 4. real provider (construction raises ModelProviderError without a key)
    if provider is None:
        provider = KimiProvider(settings, audit=audit, run_id=run_id)
    try:
        # 5. evidence extraction in small batches (Kimi reads; Python keeps provenance)
        engine = EvidenceEngine(provider)
        cards: list[EvidenceCard] = []
        for i in range(0, len(news), _EVIDENCE_BATCH):
            cards.extend(await engine.extract(news[i : i + _EVIDENCE_BATCH], as_of))
        if not cards:
            raise ResearchRuntimeError(
                "Kimi extracted no usable evidence from the gathered news — refusing "
                "to freeze an empty snapshot"
            )

        # 6. theses + validation debate + scoring per sector (same components as the demo)
        orchestrator = AgentOrchestrator(provider, audit=audit)
        scoring_cfg = load_scoring_config()
        label_to_id = {label: sid for sid, label in sector_labels.items()}
        submissions: list[SectorSubmission] = []
        for sector, sector_cards in sorted(group_evidence_by_sector(cards).items()):
            package = EvidencePackage(
                run_id=run_id,
                as_of_date=as_of,
                evidence=sector_cards,
                news=news,
                market_features_ref=f"features_{run_id}",
            )
            argued = await orchestrator.run(package, agent_names=["sector"])
            thesis = build_thesis(sector, sector_cards, argued.arguments.get("sector"), as_of)
            validation = await validate_thesis(thesis, package, provider, audit=audit)

            sector_features = features[features["ticker"].isin(thesis.candidate_securities)]
            components = {
                "trend_strength": thesis.confidence,
                "evidence_quality": min(
                    1.0, sum(c.confidence * c.relevance for c in sector_cards) / len(sector_cards)
                ),
                "supply_chain_confidence": 0.5
                if any(c.category.value == "supply_bottleneck" for c in sector_cards)
                else 0.3,
                "market_confirmation": float(sector_features["rank_ret_63d"].mean())
                if not sector_features.empty and pd.notna(sector_features["rank_ret_63d"].mean())
                else 0.0,
                "fundamental_confirmation": 0.5,
                "valuation_risk": 0.3,
                "crowding_risk": 0.3,
                "liquidity": float(sector_features["rank_dollar_volume"].mean())
                if not sector_features.empty and pd.notna(sector_features["rank_dollar_volume"].mean())
                else 0.0,
                "macro_alignment": 0.6,
                "validation_strength": validation.score,
            }
            scores = compute_score(components, scoring_cfg)
            submissions.append(
                SectorSubmission(
                    thesis=thesis,
                    validation=validation,
                    scores=scores,
                    composite_score=scores.composite,
                )
            )

        # 7. ranking → mapping/tradability → signals → portfolio + risk
        ranking = rank_sectors(submissions, run_id, as_of, scoring_cfg)
        selected = {r.sector for r in ranking.leaderboard if r.selected}
        mappings, etf_map, tradability = {}, {}, {}
        for sub in submissions:
            label = sub.thesis.sector
            sector_id = label_to_id.get(label)
            if sector_id is None:
                continue
            mappings[label] = map_sector_securities(
                sector_id, label, as_of, evidence_tickers=set(sub.thesis.candidate_securities)
            )
            etf_map[label] = [e.etf_ticker for e in map_sector_etfs(sector_id, label, as_of)]
        if selected:
            candidate_tickers = sorted(
                {m.ticker for label in selected for m in mappings.get(label, [])}
                | {t for label in selected for t in etf_map.get(label, [])}
            )
            for ticker in candidate_tickers:
                try:
                    ticker_bars = repo.get_bars(context, [ticker], start, as_of)
                except DataValidationError:
                    ticker_bars = []  # no data -> cannot prove tradability
                tradability[ticker] = check_tradability(ticker, ticker_bars, as_of)
        signal_package = build_signals(submissions, ranking, mappings, tradability, etf_map, audit=audit)
        target = build_strategy("ensemble", signal_package.actionable, features, run_id, as_of)
        target = apply_risk_constraints(target, features=features)

        # 8. freeze BEFORE the future opens
        news_files = (
            sorted(p for p in news_dir.iterdir() if p.suffix.lower() in _NEWS_SUFFIXES)
            if news_dir.is_dir()
            else []
        )
        features_file = store.dir("features") / f"features_{run_id}.parquet"
        provider_name = getattr(provider, "name", "unknown")
        snapshot = freeze_snapshot(
            context,
            ranking=ranking,
            signals=signal_package,
            portfolio=target,
            active_thesis_ids=[s.thesis.thesis_id for s in submissions],
            evidence_ids=[c.evidence_id for c in cards],
            configs=load_all_configs(),
            data_files=[*news_files, features_file],
            model_versions={
                "provider": str(provider_name),
                "model": str(getattr(provider, "model", provider_name)),
                "data_source": source_name,
                "news_providers": ",".join(s for s in ("newscatcher", "bloomberg_export") if news_sources[s]),
            },
            universe_methodology=(
                "static_configured_universe (configs/universe.yaml) — historical "
                "results are survivorship-biased; no point-in-time constituents"
            ),
            warnings=warnings + signal_package.warnings,
            store=store,
            audit=audit,
        )

        # 9. backtest strictly AFTER the freeze, on already-fetched history (no refetch)
        backtest_metrics = benchmarks = None
        failure = None
        if with_backtest and has_test_data:
            test_window = FutureDataGate(context=context, store=store).open_test_window()
            full_df = pd.DataFrame([b.model_dump() for b in full_bars])
            full_df["timestamp"] = pd.to_datetime(full_df["timestamp"], utc=True)
            test_prices = full_df[full_df["timestamp"] >= pd.Timestamp(test_window[0])]
            split = WalkForwardSplit(
                split_id=f"split_{run_id}",
                lookback_start=start,
                as_of_date=as_of,
                test_start=test_start,
                test_end=test_end,
            )
            result = run_backtest(
                snapshot,
                split,
                test_prices,
                BacktestConfig(benchmarks=["SPY"]),
                store=store,
                audit=audit,
            )
            backtest_metrics = result.metrics.model_dump()
            benchmarks = result.benchmarks
            warnings.extend(result.warnings)
            if result.metrics.cumulative_return < result.benchmarks.get("SPY", 0.0):
                failure = build_failure_record(snapshot, result, benchmark_cum=result.benchmarks.get("SPY"))
                store.save_model("analysis", failure.failure_id, failure)

        tracker = getattr(provider, "tracker", None)
        return {
            "run_id": run_id,
            "as_of_date": as_of.isoformat(),
            "test_window": (
                [test_start.isoformat(), test_end.isoformat()]
                if test_start is not None and test_end is not None
                else None
            ),
            "data_source": source_name,
            "provider": str(provider_name),
            "news_dir": str(news_dir),
            "bars_visible": len(bars),
            "news_visible": len(news),
            "news_sources": news_sources,
            **({"newscatcher": dict(nc_stats)} if newscatcher_active else {}),
            "evidence_cards": len(cards),
            "theses": len(submissions),
            "selected_sectors": sorted(selected),
            "selection_rationale": ranking.selection_rationale,
            "portfolio_positions": len(target.positions),
            "cash_weight": target.cash_weight,
            "snapshot_id": snapshot.snapshot_id,
            "backtest": backtest_metrics,
            "benchmarks": benchmarks,
            "failure_record": failure.failure_id if failure else None,
            "model_cost_usd": tracker.cost_usd if tracker is not None else None,
            "model_calls": tracker.calls if tracker is not None else None,
            "warnings": warnings + signal_package.warnings,
        }
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()
