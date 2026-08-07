"""Backtest metrics: Sharpe/Sortino/drawdown/hit rate/IR. Pure pandas."""

from __future__ import annotations

import math

import pandas as pd

from quant_platform.core.schemas import BacktestMetrics

TRADING_DAYS_PER_YEAR = 252


def _max_drawdown(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns).cumprod()
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def compute_metrics(
    daily_returns: pd.Series,
    risk_free_annual: float = 0.04,
    benchmark_returns: dict[str, pd.Series] | None = None,
    turnover: float = 0.0,
    transaction_costs: float = 0.0,
) -> BacktestMetrics:
    """Standard daily-return metrics. Empty input → all-zero metrics."""
    rets = daily_returns.dropna()
    if rets.empty:
        return BacktestMetrics(turnover=turnover, transaction_costs=transaction_costs)

    n = len(rets)
    cumulative = float((1.0 + rets).prod() - 1.0)
    annualized = float((1.0 + cumulative) ** (TRADING_DAYS_PER_YEAR / n) - 1.0)
    vol = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)) if n > 1 else 0.0

    rf_daily = risk_free_annual / TRADING_DAYS_PER_YEAR
    excess = rets - rf_daily
    std = float(rets.std(ddof=1)) if n > 1 else 0.0
    sharpe = (
        float(excess.mean() / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 1e-12 else 0.0
    )
    downside = rets[rets < rf_daily] - rf_daily
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (
        float(excess.mean() / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))
        if downside_std > 1e-12
        else 0.0
    )

    benchmark_excess: dict[str, float] = {}
    information_ratio = None
    for name, bench in (benchmark_returns or {}).items():
        aligned = bench.reindex(rets.index).dropna()
        if aligned.empty:
            continue
        bench_cum = float((1.0 + aligned).prod() - 1.0)
        port_cum = float((1.0 + rets.reindex(aligned.index)).prod() - 1.0)
        benchmark_excess[name] = port_cum - bench_cum
        if information_ratio is None:  # first benchmark = primary
            diff = rets.reindex(aligned.index) - aligned
            if len(diff) > 1 and diff.std(ddof=1) > 1e-12:
                information_ratio = float(
                    diff.mean() / diff.std(ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR)
                )

    return BacktestMetrics(
        cumulative_return=cumulative,
        annualized_return=annualized,
        annualized_volatility=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=_max_drawdown(rets),
        hit_rate=float((rets > 0).mean()),
        turnover=turnover,
        transaction_costs=transaction_costs,
        benchmark_excess_return=benchmark_excess,
        information_ratio=information_ratio,
    )
