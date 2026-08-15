"""Prediction snapshot and backtesting schemas."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import Field

from quant_platform.core.enums import PlatformModel
from quant_platform.core.schemas.portfolio import PortfolioTarget
from quant_platform.core.schemas.research import RankingResult
from quant_platform.core.schemas.signals import SignalPackage


class PredictionSnapshot(PlatformModel, frozen=True):
    """Immutable frozen decision. Created BEFORE future data is opened; the
    evaluation layer must prove it works from this snapshot and nothing else."""

    snapshot_id: str
    run_id: str
    as_of_date: date
    visible_cutoff: str  # ISO instant of exact visible-data cutoff
    cutoff_timezone: str = ""  # market timezone the cutoff was localized in
    test_start: date | None = None  # evaluation metadata — may be unknown at freeze
    test_end: date | None = None
    active_thesis_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    ranking: RankingResult | None = None
    signals: SignalPackage | None = None
    portfolio: PortfolioTarget | None = None
    model_versions: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    config_hash: str = ""
    data_snapshot_hash: str = ""
    universe_methodology: str = ""
    warnings: list[str] = Field(default_factory=list)
    integrity_hash: str = ""  # self-hash over the canonical JSON (see below)
    frozen_at: str = ""  # ISO instant when frozen


def snapshot_integrity_hash(snapshot: PredictionSnapshot) -> str:
    """sha256 over the canonical JSON of the snapshot, integrity_hash excluded."""
    payload = snapshot.model_dump(mode="json")
    payload.pop("integrity_hash", None)
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class WalkForwardSplit(PlatformModel):
    split_id: str
    lookback_start: date
    as_of_date: date  # research cutoff == end of visible window
    test_start: date
    test_end: date


class BacktestMetrics(PlatformModel):
    cumulative_return: float = 0.0
    annualized_return: float = 0.0
    annualized_volatility: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    hit_rate: float = 0.0
    turnover: float = 0.0
    transaction_costs: float = 0.0
    benchmark_excess_return: dict[str, float] = Field(default_factory=dict)
    information_ratio: float | None = None


class TradeContribution(PlatformModel):
    ticker: str
    sector: str = ""
    pnl: float = 0.0
    return_contribution: float = 0.0
    avg_weight: float = 0.0
    holding_days: int = 0


class BacktestResult(PlatformModel):
    result_id: str
    snapshot_id: str
    split: WalkForwardSplit
    metrics: BacktestMetrics
    contributions: list[TradeContribution] = Field(default_factory=list)
    benchmarks: dict[str, float] = Field(default_factory=dict)  # name -> cum return
    daily_returns_path: str = ""  # parquet artifact pointer
    warnings: list[str] = Field(default_factory=list)


class HorizonPerformance(PlatformModel):
    """Frozen-portfolio vs benchmark returns over one standard horizon."""

    horizon: str  # 1M | 2M | 3M | 6M | 1Y | LATEST
    end_date: str  # last market date actually used (never a blind calendar date)
    portfolio_return: float
    benchmark_returns: dict[str, float] = Field(default_factory=dict)


class SnapshotEvaluation(PlatformModel):
    """Result of evaluating ONE frozen snapshot forward — never reruns research."""

    evaluation_id: str
    snapshot_id: str
    run_id: str
    as_of_date: date
    visible_cutoff: str
    cutoff_timezone: str = ""
    entry_date: str  # next eligible session actually traded
    execution_convention: str = "next_session_close"
    holding_convention: str = "buy_and_hold_frozen"
    horizons: list[HorizonPerformance] = Field(default_factory=list)
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    cost_drag: float = 0.0  # entry costs as a fraction of notional
    contributors: dict[str, float] = Field(default_factory=dict)  # per-ticker, full window
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
