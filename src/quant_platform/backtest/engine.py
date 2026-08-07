"""Walk-forward backtest engine with realistic execution assumptions.

The engine consumes ONLY a frozen PredictionSnapshot plus a price frame —
it never re-runs research. Realism rules (configs/backtest.yaml):

- execution delay: signals trade ``execution_delay_days`` after the test
  window opens, at that day's close — never same-bar;
- costs: per-order commission (per share, with a per-order minimum) plus
  slippage in bps on traded notional; shorts additionally pay borrow;
- cash earns ``cash_return_annual`` while uninvested;
- ``per_split`` rebalance: weights are set once at entry and drift with prices.

Results carry per-ticker contributions, benchmark comparisons, and the two
configured baselines (equal-weight universe, simple 63d momentum top-3 —
computed only from pre-test data).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from pydantic import Field

from quant_platform.backtest.metrics import TRADING_DAYS_PER_YEAR, compute_metrics
from quant_platform.core.audit import AuditLogger
from quant_platform.core.config import load_yaml_config
from quant_platform.core.enums import AuditEventType, PlatformModel
from quant_platform.core.ids import stable_id
from quant_platform.core.schemas import (
    BacktestResult,
    PredictionSnapshot,
    TradeContribution,
    WalkForwardSplit,
)
from quant_platform.core.store import ArtifactStore


class BacktestConfig(PlatformModel):
    """Backtest realism settings (defaults mirror configs/backtest.yaml)."""

    commission_per_share: float = 0.005
    min_commission_per_order: float = 1.0
    slippage_bps: float = 5.0
    short_borrow_annual: float = 0.005
    cash_return_annual: float = 0.04
    execution_delay_days: int = 1
    rebalance: str = "per_split"
    max_turnover_per_rebalance: float = 1.0
    risk_free_rate_annual: float = 0.04
    benchmarks: list[str] = Field(default_factory=lambda: ["SPY", "QQQ", "SMH", "SOXX"])
    notional: float = 1_000_000  # normalizes commission math; returns are unit-free


def load_backtest_config() -> BacktestConfig:
    raw = load_yaml_config("backtest").get("backtest", {}) or {}
    bench = load_yaml_config("benchmarks")
    return BacktestConfig(
        **raw,
        risk_free_rate_annual=bench.get("risk_free_rate_annual", 0.04),
        benchmarks=list(bench.get("benchmarks", {}).get("primary", [])) or ["SPY"],
    )


def _entry_date(available: pd.DatetimeIndex, split: WalkForwardSplit, delay_days: int) -> date | None:
    earliest = pd.Timestamp(split.test_start, tz="UTC") + pd.Timedelta(days=delay_days)
    candidates = available[available >= earliest]
    return candidates.min().date() if len(candidates) else None


def run_backtest(
    snapshot: PredictionSnapshot,
    split: WalkForwardSplit,
    prices: pd.DataFrame,
    config: BacktestConfig | None = None,
    store: ArtifactStore | None = None,
    audit: AuditLogger | None = None,
) -> BacktestResult:
    """Evaluate one frozen snapshot over one split's test window.

    ``prices``: tidy frame with columns ticker/timestamp/close, expected to
    cover the test window (opened via FutureDataGate upstream).
    """
    cfg = config or BacktestConfig()
    warnings: list[str] = []
    if audit is not None:
        audit.record(
            AuditEventType.BACKTEST_STARTED,
            run_id=snapshot.run_id,
            as_of_date=snapshot.as_of_date.isoformat(),
            snapshot_id=snapshot.snapshot_id,
            split_id=split.split_id,
        )

    work = prices.copy()
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True)
    test_start = pd.Timestamp(split.test_start, tz="UTC")
    test_end = pd.Timestamp(split.test_end, tz="UTC") + pd.Timedelta(days=1)

    close = work.pivot_table(index="timestamp", columns="ticker", values="close").sort_index()
    test_close = close[(close.index >= test_start) & (close.index < test_end)]
    if test_close.empty:
        raise ValueError(f"no price data inside test window {split.test_start}..{split.test_end}")

    entry = _entry_date(test_close.index.normalize(), split, cfg.execution_delay_days)
    benchmark_returns: dict[str, pd.Series] = {}
    benchmarks_cum: dict[str, float] = {}
    for name in cfg.benchmarks:
        if name in test_close.columns:
            series = test_close[name].dropna()
            if len(series) > 1:
                benchmark_returns[name] = series.pct_change().dropna()
                benchmarks_cum[name] = float(series.iloc[-1] / series.iloc[0] - 1.0)
        else:
            warnings.append(f"benchmark {name} not in price frame — skipped")

    # baselines (honest, simple)
    universe_cum = test_close.apply(lambda s: s.dropna().iloc[-1] / s.dropna().iloc[0] - 1.0)
    benchmarks_cum["equal_weight_universe"] = float(universe_cum.mean())
    pre_test = close[close.index < test_start].tail(64)
    if len(pre_test) >= 64:
        momentum = pre_test.iloc[-1] / pre_test.iloc[0] - 1.0
        top3 = momentum.dropna().nlargest(3).index
        benchmarks_cum["simple_momentum"] = float(universe_cum[top3].mean()) if len(top3) else 0.0
    else:
        warnings.append("insufficient pre-test history for simple_momentum baseline")

    portfolio = snapshot.portfolio
    if portfolio is None or not portfolio.positions or entry is None:
        warnings.append("no positions (or no entry bar) — flat cash over the window")
        daily = pd.Series(cfg.cash_return_annual / TRADING_DAYS_PER_YEAR,
                          index=test_close.index[1:])
        result = BacktestResult(
            result_id=stable_id("bt", snapshot.snapshot_id, split.split_id),
            snapshot_id=snapshot.snapshot_id,
            split=split,
            metrics=compute_metrics(daily, cfg.risk_free_rate_annual, benchmark_returns),
            contributions=[],
            benchmarks=benchmarks_cum,
            warnings=warnings,
        )
    else:
        entry_ts = pd.Timestamp(entry, tz="UTC")
        holding = test_close[test_close.index >= entry_ts]
        entry_prices = holding.iloc[0]

        shares: dict[str, float] = {}
        costs = 0.0
        turnover = 0.0
        for pos in portfolio.positions:
            price = entry_prices.get(pos.ticker)
            if price is None or pd.isna(price) or price <= 0:
                warnings.append(f"{pos.ticker}: no entry price at {entry} — excluded")
                continue
            qty = pos.weight * cfg.notional / float(price)
            shares[pos.ticker] = qty
            turnover += abs(pos.weight)
            commission = max(cfg.min_commission_per_order, abs(qty) * cfg.commission_per_share)
            slippage = abs(pos.weight) * cfg.slippage_bps / 10_000 * cfg.notional
            costs += commission + slippage

        cash = cfg.notional * portfolio.cash_weight - costs
        tickers = [t for t in shares if t in holding.columns]
        values = holding[tickers].mul(pd.Series(shares)[tickers], axis=1).sum(axis=1)
        equity = values + cash
        borrow_daily = cfg.short_borrow_annual / TRADING_DAYS_PER_YEAR
        short_notional = sum(-q * float(entry_prices[t]) for t, q in shares.items() if q < 0)
        daily = equity.pct_change()
        # entry day return includes the up-front cost hit vs starting notional
        daily.iloc[0] = equity.iloc[0] / cfg.notional - 1.0
        daily = daily + cfg.cash_return_annual / TRADING_DAYS_PER_YEAR * portfolio.cash_weight
        if short_notional > 0:
            daily = daily - borrow_daily * short_notional / cfg.notional

        contributions = []
        for pos in portfolio.positions:
            if pos.ticker not in shares or pos.ticker not in holding.columns:
                continue
            series = holding[pos.ticker].dropna()
            if len(series) < 2:
                continue
            ticker_ret = float(series.iloc[-1] / series.iloc[0] - 1.0)
            contributions.append(
                TradeContribution(
                    ticker=pos.ticker,
                    sector=pos.sector,
                    pnl=pos.weight * cfg.notional * ticker_ret,
                    return_contribution=pos.weight * ticker_ret,
                    avg_weight=pos.weight,
                    holding_days=len(series),
                )
            )

        result = BacktestResult(
            result_id=stable_id("bt", snapshot.snapshot_id, split.split_id),
            snapshot_id=snapshot.snapshot_id,
            split=split,
            metrics=compute_metrics(
                daily, cfg.risk_free_rate_annual, benchmark_returns,
                turnover=turnover, transaction_costs=costs / cfg.notional,
            ),
            contributions=contributions,
            benchmarks=benchmarks_cum,
            warnings=warnings,
        )
        if store is not None:
            path = store.save_table(
                "backtests", f"daily_{result.result_id}", daily.rename("return").reset_index()
            )
            result = result.model_copy(update={"daily_returns_path": str(path)})

    if store is not None:
        store.save_model("backtests", result.result_id, result)
    if audit is not None:
        audit.record(
            AuditEventType.BACKTEST_COMPLETED,
            run_id=snapshot.run_id,
            as_of_date=snapshot.as_of_date.isoformat(),
            result_id=result.result_id,
            cumulative_return=result.metrics.cumulative_return,
            sharpe=result.metrics.sharpe,
            max_drawdown=result.metrics.max_drawdown,
        )
    return result
