"""End-to-end OFFLINE demo pipeline: synthetic data → frozen snapshot → backtest.

Runs the whole platform on clearly-marked SYNTHETIC data with the
MockModelProvider — no Bloomberg, no Kimi key, no broker needed. Every stage
is the real code path (gatekeeper-filtered repository, feature engine,
evidence engine, thesis builder, validation debate, scoring, ranking,
signals, portfolio+risk, snapshot freeze, walk-forward backtest), so the
demo doubles as an integration test of the invariants:

- all data flows through the gatekeeper / FutureDataGate;
- sector signals stay labels; only tradable securities become actionable;
- Python does all math; the mock LLM only fills language slots.
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
from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import SourceType
from quant_platform.core.gatekeeper import FutureDataGate, ResearchContext, TimeGatekeeper
from quant_platform.core.schemas import (
    EvidencePackage,
    NewsRecord,
    SectorSubmission,
    WalkForwardSplit,
)
from quant_platform.core.store import ArtifactStore
from quant_platform.core.timeutil import start_of_day_utc, utc_now
from quant_platform.data.bloomberg_export import BloombergExportAdapter
from quant_platform.data.repository import PITRepository
from quant_platform.data.sample_data import generate_sample_export, generate_sample_news
from quant_platform.data.validation import DataValidationError
from quant_platform.features.engine import compute_features
from quant_platform.models import MockModelProvider
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
from quant_platform.signals import build_signals
from quant_platform.snapshots import freeze_snapshot


def _sector_label_map() -> tuple[dict[str, str], dict[str, str]]:
    """ticker -> sector label, sector_id -> label (from configs)."""
    universe = load_yaml_config("universe").get("universe", {})
    sectors = {s["id"]: s["label"] for s in load_yaml_config("sectors").get("sectors", [])}
    ticker_to_label = {}
    for sector_id, entry in universe.items():
        label = sectors.get(sector_id, sector_id)
        for ticker in entry.get("securities", []):
            ticker_to_label.setdefault(ticker, label)
    return ticker_to_label, sectors


def _load_news(news_csv: Path, ticker_to_label: dict[str, str]) -> list[NewsRecord]:
    df = pd.read_csv(news_csv)
    records = []
    for row in df.itertuples():
        ticker = str(row.security).replace(" US Equity", "")
        published = start_of_day_utc(date.fromisoformat(str(row.date)))
        records.append(
            NewsRecord(
                news_id=f"demo_{ticker}_{row.date}",
                source=SourceType.SYNTHETIC,
                source_ref=str(news_csv),
                headline=str(row.headline),
                body=str(getattr(row, "body", "")),
                securities=[ticker],
                sectors=[ticker_to_label[ticker]] if ticker in ticker_to_label else [],
                published_at=published,
                usable_from=published,
                retrieved_at=utc_now(),
            )
        )
    return records


async def run_demo(
    data_root: Path | str,
    seed: int = 42,
    tickers: list[str] | None = None,
    history_days: int = 400,
    audit: AuditLogger | None = None,
) -> dict[str, Any]:
    """Run the full pipeline offline. Returns a summary dict of artifacts."""
    data_root = Path(data_root)
    store = ArtifactStore(data_root)
    run_id = f"demo_{seed}"
    ticker_to_label, _sectors = _sector_label_map()
    tickers = tickers or ["NVDA", "AVGO", "MU", "VRT", "AMD", "SPY"]

    # 1. synthetic sample data (clearly marked)
    export_dir = data_root / "raw" / "demo_exports"
    end = date.today()
    start = end - timedelta(days=history_days)
    prices_csv = generate_sample_export(
        export_dir, tickers=tickers, start=start.isoformat(), end=end.isoformat(), seed=seed
    )
    news_csv = generate_sample_news(
        export_dir, tickers=tickers, start=start.isoformat(), end=end.isoformat(),
        seed=seed, per_ticker=6,
    )

    # 2. research context: as_of 63 trading days (~3 months) before the end
    all_days = pd.bdate_range(start, end)
    as_of = all_days[-64].date()
    test_start, test_end = all_days[-63].date(), all_days[-1].date()
    context = ResearchContext(
        run_id=run_id, as_of_date=as_of, visible_start=start, visible_end=as_of,
        test_start=test_start, test_end=test_end,
    )

    # 3. gatekeeper-filtered data access
    adapter = BloombergExportAdapter(export_dir)
    repo = PITRepository(adapter, store=store, audit=audit)
    bars = repo.get_bars(context, tickers, start, as_of)
    df = pd.DataFrame([b.model_dump() for b in bars])
    features = compute_features(df, as_of, benchmark="SPY" if "SPY" in tickers else tickers[0])
    store.save_table("features", f"features_{run_id}", features)

    gate = TimeGatekeeper(context, audit=audit)
    news = gate.filter_by_usable_from(_load_news(news_csv, ticker_to_label), what="news_record")

    # 4. evidence extraction (mock LLM does the reading; Python does provenance)
    visible = news[-8:]
    scripted = {
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
            "agent_name": "sector", "conclusion": "synthetic demand trend intact",
            "confidence": 0.85, "direction": "positive", "as_of_date": as_of.isoformat(),
        },
        "judge": {
            "agent_name": "judge", "conclusion": "bull case stronger on synthetic evidence",
            "confidence": 0.8, "direction": "positive", "as_of_date": as_of.isoformat(),
        },
    }
    provider = MockModelProvider(scripted=scripted)
    cards = await EvidenceEngine(provider).extract(visible, as_of)

    # 5. theses + validation debate + scoring per sector
    orchestrator = AgentOrchestrator(provider, audit=audit)
    scoring_cfg = load_scoring_config()
    submissions: list[SectorSubmission] = []
    for sector, sector_cards in sorted(group_evidence_by_sector(cards).items()):
        package = EvidencePackage(
            run_id=run_id, as_of_date=as_of, evidence=sector_cards, news=visible,
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
            "supply_chain_confidence": 0.5 if any(
                c.category.value == "supply_bottleneck" for c in sector_cards
            ) else 0.3,
            "market_confirmation": float(sector_features["rank_ret_63d"].mean())
            if not sector_features.empty
            and pd.notna(sector_features["rank_ret_63d"].mean())
            else 0.0,
            "fundamental_confirmation": 0.5,
            "valuation_risk": 0.3,
            "crowding_risk": 0.3,
            "liquidity": float(sector_features["rank_dollar_volume"].mean())
            if not sector_features.empty
            and pd.notna(sector_features["rank_dollar_volume"].mean())
            else 0.0,
            "macro_alignment": 0.6,
            "validation_strength": validation.score,
        }
        scores = compute_score(components, scoring_cfg)
        submissions.append(
            SectorSubmission(
                thesis=thesis, validation=validation, scores=scores,
                composite_score=scores.composite,
            )
        )

    # 6. ranking → signals → portfolio + risk
    ranking = rank_sectors(submissions, run_id, as_of, scoring_cfg)
    selected = {r.sector for r in ranking.leaderboard if r.selected}
    mappings, etf_map, tradability = {}, {}, {}
    for sub in submissions:
        label = sub.thesis.sector
        sector_id = next(
            (sid for sid, e in load_yaml_config("universe").get("universe", {}).items()
             if _sector_label_map()[1].get(sid) == label),
            None,
        )
        if sector_id is None:
            continue
        mappings[label] = map_sector_securities(
            sector_id, label, as_of, evidence_tickers=set(sub.thesis.candidate_securities)
        )
        etf_map[label] = [e.etf_ticker for e in map_sector_etfs(sector_id, label, as_of)]
    if selected:
        candidate_tickers = sorted({
            m.ticker for label in selected for m in mappings.get(label, [])
        } | {t for label in selected for t in etf_map.get(label, [])})
        for ticker in candidate_tickers:
            try:
                ticker_bars = repo.get_bars(context, [ticker], start, as_of)
            except DataValidationError:
                ticker_bars = []  # no data -> cannot prove tradability
            tradability[ticker] = check_tradability(ticker, ticker_bars, as_of)
    signal_package = build_signals(
        submissions, ranking, mappings, tradability, etf_map, audit=audit
    )
    target = build_strategy("ensemble", signal_package.actionable, features, run_id, as_of)
    target = apply_risk_constraints(target, features=features)

    # 7. freeze BEFORE the future opens; then evaluate on the test window
    snapshot = freeze_snapshot(
        context, ranking=ranking, signals=signal_package, portfolio=target,
        active_thesis_ids=[s.thesis.thesis_id for s in submissions],
        evidence_ids=[c.evidence_id for c in cards],
        configs={"scoring": load_yaml_config("scoring"), "universe": load_yaml_config("universe")},
        data_files=[prices_csv], model_versions={"provider": "mock"},
        store=store, audit=audit,
    )
    test_window = FutureDataGate(context=context, snapshot_frozen=True).open_test_window()
    full_df = pd.DataFrame([b.model_dump() for b in adapter.get_history(tickers, start, end)])
    full_df["timestamp"] = pd.to_datetime(full_df["timestamp"], utc=True)
    test_prices = full_df[full_df["timestamp"] >= pd.Timestamp(test_window[0])]
    split = WalkForwardSplit(
        split_id=f"split_{run_id}", lookback_start=start, as_of_date=as_of,
        test_start=test_start, test_end=test_end,
    )
    result = run_backtest(
        snapshot, split, test_prices, BacktestConfig(benchmarks=["SPY"]),
        store=store, audit=audit,
    )

    # 8. failure analysis when the outcome disappoints (never rewrites history)
    failure = None
    if result.metrics.cumulative_return < result.benchmarks.get("SPY", 0.0):
        failure = build_failure_record(
            snapshot, result, benchmark_cum=result.benchmarks.get("SPY")
        )
        store.save_model("analysis", failure.failure_id, failure)

    return {
        "run_id": run_id,
        "as_of_date": as_of.isoformat(),
        "test_window": [test_start.isoformat(), test_end.isoformat()],
        "bars_visible": len(bars),
        "news_visible": len(news),
        "evidence_cards": len(cards),
        "theses": len(submissions),
        "selected_sectors": sorted(selected),
        "selection_rationale": ranking.selection_rationale,
        "portfolio_positions": len(target.positions),
        "cash_weight": target.cash_weight,
        "snapshot_id": snapshot.snapshot_id,
        "backtest": result.metrics.model_dump(),
        "benchmarks": result.benchmarks,
        "failure_record": failure.failure_id if failure else None,
        "warnings": signal_package.warnings + result.warnings,
    }
